# appium_bot.py
from appium.webdriver import Remote
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from config import APPIUM_CAPS
from ai_commenter import get_ai_comment
from utils import log_profile, take_screenshot

import os
import time
import re
import base64

from urllib3.exceptions import MaxRetryError, NewConnectionError

# ---------- Session (re)creation + globals ----------
APPIUM_SERVER_URL = os.environ.get("APPIUM_SERVER_URL", "http://127.0.0.1:4723")
APPIUM_CONNECT_RETRIES = int(os.environ.get("APPIUM_CONNECT_RETRIES", "45"))
APPIUM_CONNECT_DELAY = float(os.environ.get("APPIUM_CONNECT_DELAY", "1.0"))

driver = None
wait_short = None
wait = None

def is_uia2_crash_msg(msg: str) -> bool:
    if not msg:
        return False
    m = msg.lower()
    return (
        "instrumentation process is not running" in m
        or ("uiautomator2 server" in m and "not running" in m)
        or "cannot be proxied to uiautomator2 server" in m
        or "get /screenshot" in m and "not running" in m
        or "get /window/current/size" in m and "not running" in m
    )

def _connect_remote(opts: UiAutomator2Options):
    """Connexion au serveur Appium avec retries (évite Connection refused si Appium démarre après le script)."""
    last_err = None
    for attempt in range(1, APPIUM_CONNECT_RETRIES + 1):
        try:
            return Remote(APPIUM_SERVER_URL, options=opts)
        except (MaxRetryError, NewConnectionError, ConnectionRefusedError, OSError) as e:
            last_err = e
            if attempt == 1:
                print(
                    f"⏳ Appium injoignable sur {APPIUM_SERVER_URL} — "
                    f"attente du serveur (max {APPIUM_CONNECT_RETRIES} essais, "
                    f"{APPIUM_CONNECT_DELAY}s)…"
                )
            time.sleep(APPIUM_CONNECT_DELAY)
        except Exception as e:
            msg = str(e).lower()
            if "refused" in msg or "failed to establish" in msg or "max retries exceeded" in msg:
                last_err = e
                time.sleep(APPIUM_CONNECT_DELAY)
                continue
            raise
    raise RuntimeError(
        f"Impossible de joindre Appium sur {APPIUM_SERVER_URL} après "
        f"{APPIUM_CONNECT_RETRIES} tentatives. Lance `appium` dans un autre terminal, "
        f"ou définis APPIUM_SERVER_URL. Dernière erreur: {last_err!r}"
    ) from last_err


def new_session():
    """Crée (ou recrée) proprement la session Appium + waits."""
    global driver, wait_short, wait, APP_PKG
    try:
        if driver:
            driver.quit()
    except Exception:
        pass

    opts = UiAutomator2Options()
    for k, v in APPIUM_CAPS.items():
        opts.set_capability(k, v)

    driver = _connect_remote(opts)
    APP_PKG = APPIUM_CAPS["appPackage"]
    # N’envoie am start que si Hinge n’est pas déjà au premier plan (évite flash / « réouverture » inutile)
    try:
        pkg = driver.execute_script("mobile: getCurrentPackage")
        if pkg != APP_PKG:
            driver.activate_app(APP_PKG)
    except Exception:
        try:
            if getattr(driver, "current_package", None) != APP_PKG:
                driver.activate_app(APP_PKG)
        except Exception:
            try:
                driver.activate_app(APP_PKG)
            except Exception:
                try:
                    driver.start_activity(APP_PKG, "co.hinge.app.ui.AppActivity")
                except Exception:
                    pass
    time.sleep(2)
    wait_short = WebDriverWait(driver, 4)
    wait = WebDriverWait(driver, 8)

def recover_session(reason: str = ""):
    print(f"[RECOVER] Restart UiAutomator2 session... {('reason: ' + reason) if reason else ''}")
    new_session()

# ---------- Appium init ----------
new_session()
APP_PKG = APPIUM_CAPS["appPackage"]

# ---------- Watchdog foreground ----------
def is_hinge_foreground() -> bool:
    """True si Hinge est au premier plan (quelques retries sur erreur transitoire)."""
    for _ in range(3):
        try:
            pkg = driver.execute_script("mobile: getCurrentPackage")
            return pkg == APP_PKG
        except Exception:
            pass
        try:
            return driver.current_package == APP_PKG
        except Exception:
            time.sleep(0.12)
    return False

def ensure_hinge_foreground():
    if is_hinge_foreground():
        return
    try:
        driver.activate_app(APP_PKG)
        time.sleep(0.6)
        return
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("ensure_hinge_foreground/activate")
            return
    try:
        driver.start_activity(APP_PKG, ".MainActivity")
        time.sleep(0.8)
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("ensure_hinge_foreground/start_activity")

# ---------- helpers ----------
def _safe_back() -> bool:
    """Appuie sur Back et vérifie que Hinge est toujours au premier plan.
    Retourne True si l'app est restée au premier plan, False sinon
    (et dans ce cas ramène Hinge automatiquement).
    """
    try:
        driver.back()
        time.sleep(0.3)
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("safe_back")
        return False
    if not is_hinge_foreground():
        print("⚠️ driver.back() a réduit Hinge — on le ramène")
        ensure_hinge_foreground()
        return False
    return True

def _safe_get_window_size():
    try:
        return driver.get_window_size()
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("get_window_size")
        raise

def swipe_to_next():
    """Passe au profil suivant (swipe gauche).
    On inset la zone de swipe pour ne pas déclencher les gestes système Android
    (swipe depuis le bord = back/home).
    """
    try:
        sz = _safe_get_window_size()
        w, h = sz["width"], sz["height"]
        margin = int(w * 0.08)
        driver.execute_script("mobile: swipeGesture", {
            "left": margin, "top": int(h * 0.25),
            "width": w - 2 * margin, "height": int(h * 0.50),
            "direction": "left", "percent": 0.75
        })
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("swipe swipeGesture")
            return
        try:
            sz = _safe_get_window_size()
            w, h = sz["width"], sz["height"]
            driver.execute_script("mobile: dragGesture", {
                "startX": int(w*0.75), "startY": int(h*0.50),
                "endX": int(w*0.25), "endY": int(h*0.50), "speed": 800
            })
        except Exception as ee:
            if is_uia2_crash_msg(str(ee)):
                recover_session("swipe dragGesture")
    time.sleep(0.8)

def _page_source_lower(max_chars: int = 500_000) -> str:
    """Fallback quand find_elements renvoie [] (Compose, délai, etc.)."""
    try:
        src = driver.page_source or ""
        if len(src) > max_chars:
            src = src[:max_chars]
        return src.lower()
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("_page_source_lower")
        return ""


def _page_source_suggests_like_overlay() -> bool:
    s = _page_source_lower()
    if len(s) < 50:
        return False
    return (
        "send like" in s
        or "send priority like" in s
        or "comment_composition" in s
        or "comment_composition_view" in s
        or "add a comment" in s
        or "add comment" in s
    )


# Fragments (minuscules) repérés dans les questions Hinge — utilisés sur page_source quand les TextView manquent (Compose).
_PROMPT_HEADER_MARKERS = (
    "together, we could",
    "i get along",
    "green flags",
    "my simple pleasures",
    "dating me is like",
    "i want someone who",
    "the way to win me over",
    "my most irrational fear",
    "we'll get along if",
    "we will get along if",
    "i go crazy",
    "go crazy for",
    "typical sunday",
    "unusual skills",
    "my mantra",
    "fact about me",
    "don't hate me if",
    "dont hate me if",
    "best first date",
    "worst first date",
    "i'm looking for",
    "im looking for",
    "change my mind",
    "two truths and a lie",
    "two truths",
    "never have i",
    "weirdest gift",
    "most embarrassing",
    "this year i",
    "the one thing",
    "my ideal date",
    "pet peeves",
    "simple pleasures",
    "together we should",
    "guess where",
    "shoot your shot",
    "trying to",
)

_PAGE_SOURCE_UI_EXACT = frozenset(
    {
        "like",
        "send like",
        "send priority like",
        "cancel",
        "close",
        "back",
        "navigate up",
        "add a comment",
        "add comment",
        "skip",
        "pass",
        "discover",
        "likes you",
        "standouts",
        "settings",
        "done",
        "next",
        "photo",
        "video",
    }
)

# Un seul mot souvent présent entre le titre du prompt et la vraie réponse (ex. pronoms affichés à part).
_PAGE_SOURCE_SKIP_ONE_WORD_ANSWER = frozenset(
    {
        "ella",
        "el",
        "la",
        "los",
        "las",
        "le",
        "yo",
        "tu",
        "tú",
    }
)


def wait_profile_ready(timeout_sec: float = 3.0):
    end = time.time() + timeout_sec
    while time.time() < end:
        try:
            # Coeur principal (accessibility_id ou content-desc)
            if driver.find_elements(AppiumBy.ACCESSIBILITY_ID, "Like"):
                return True
            # Prompt card visible
            if driver.find_elements(By.ID, "co.hinge.app:id/prompt_bubble"):
                return True
            # XPath fallback : tout bouton avec content-desc "Like" qui n'est pas "Send like"
            like_els = driver.find_elements(
                By.XPATH,
                "//*[(@content-desc='Like' or @content-desc='like') "
                "and not(contains(translate(@content-desc,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'send'))]"
            )
            if like_els:
                return True
            # Photo principale chargée (RecyclerView de profil)
            if driver.find_elements(By.ID, "co.hinge.app:id/photo_pager"):
                return True
            if "co.hinge.app:id/" in _page_source_lower(120_000):
                return True
        except Exception as e:
            if is_uia2_crash_msg(str(e)):
                recover_session("wait_profile_ready")
                return False
        time.sleep(0.2)
    return False

def close_overlay_if_open():
    """
    Ferme un overlay si présent SANS risquer de quitter l'app.
    """
    try:
        if not is_hinge_foreground():
            return False

        close_btns = driver.find_elements(
            By.XPATH,
            "//*[@content-desc='Close' or @content-desc='Navigate up' or @text='Close' or @content-desc='Back']"
        )
        if close_btns:
            try:
                close_btns[0].click()
                time.sleep(0.3)
                return True
            except Exception as e:
                if is_uia2_crash_msg(str(e)):
                    recover_session("close_overlay click close")
                    return False

        _safe_back()
        return True
    except Exception:
        pass
    return False

def detect_end_of_stack():
    markers = [
        "come back later", "no more", "that's all for now",
        "out of likes", "you're out of likes", "check back soon",
        "all caught up", "try again later",
    ]
    try:
        tvs = driver.find_elements(By.XPATH, "//android.widget.TextView")
        content = " | ".join([t.text for t in tvs if t.text])
        low = content.lower()
        if any(m in low for m in markers):
            return True
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("detect_end_of_stack")
            return False
    src = _page_source_lower()
    return any(m in src for m in markers)


def detect_hinge_reply_queue_limit():
    """
    Hinge peut refuser d'envoyer un like tant que trop de conversations
    attendent une réponse (texte du type « You're over the limit »).
    Dans cet état, l'overlay commentaire / Send priority like n'apparaît pas.
    """
    markers = [
        "you're over the limit",
        "you are over the limit",
        "when 8 or more people are waiting",
        "when eight or more people are waiting",
    ]
    try:
        tvs = driver.find_elements(By.XPATH, "//android.widget.TextView")
        content = " | ".join([t.text for t in tvs if t.text])
        c = content.lower()
        if any(m in c for m in markers):
            return True
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("detect_hinge_reply_queue_limit")
            return False
    src = _page_source_lower()
    return any(m in src for m in markers)


def _acc_id_displayed(acc: str) -> bool:
    try:
        for e in driver.find_elements(AppiumBy.ACCESSIBILITY_ID, acc):
            try:
                if e.is_displayed():
                    return True
            except Exception:
                pass
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("acc_id_displayed")
    return False


def is_comment_overlay_open():
    try:
        if driver.find_elements(By.ID, "co.hinge.app:id/comment_composition_view"):
            return True
        # Compose : le libellé du bouton est souvent en content-desc / accessibility id, pas en @text
        if _acc_id_displayed("Send priority like") or _acc_id_displayed("Send like"):
            return True
        if driver.find_elements(By.XPATH,
            "//*[contains(translate(@text,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'priority like') or "
            "contains(translate(@text,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'send priority')]"
        ):
            return True
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("is_comment_overlay_open")
    if "co.hinge.app:id/comment_composition_view" in _page_source_lower(200_000):
        return True
    return False


def _wait_for_like_overlay_hint(total_sec: float = 2.8) -> None:
    """Attend que l’overlay like soit visible (widgets et/ou page_source)."""
    end = time.time() + total_sec
    while time.time() < end:
        if is_comment_overlay_open() or is_buggy_overlay_state():
            return
        if _page_source_suggests_like_overlay():
            return
        time.sleep(0.2)

# ---------- NOUVELLE FONCTION : Détection état bugué ----------
def is_buggy_overlay_state():
    """
    Détecte si on est dans un état bugué où l'overlay de commentaire
    est ouvert mais mal détecté (ex: "Cancel" + nom de la personne).
    Retourne True si on détecte cet état.
    """
    try:
        # Cherche le bouton Cancel qui indique qu'on est dans l'overlay
        cancel_btns = driver.find_elements(By.XPATH,
            "//*[@text='Cancel' or @content-desc='Cancel']"
        )
        if cancel_btns:
            return True
        
        # Cherche un EditText visible (champ de saisie commentaire)
        edit_fields = driver.find_elements(By.XPATH, "//android.widget.EditText")
        if edit_fields:
            # Vérifie si c'est bien l'overlay de commentaire
            for field in edit_fields:
                try:
                    if field.is_displayed():
                        return True
                except:
                    pass
        
        # Cherche "Send priority like" ou "Send like" visible
        send_btns = driver.find_elements(By.XPATH,
            "//*[contains(@text,'Send') and contains(@text,'like')]"
        )
        if send_btns:
            return True
        if _acc_id_displayed("Send priority like") or _acc_id_displayed("Send like"):
            return True

    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("is_buggy_overlay_state")
    return False

# Limite de caractères pour les commentaires Hinge
MAX_COMMENT_LENGTH = 150

def get_input_text_length():
    """
    Retourne la longueur du texte dans l'input de commentaire.
    Retourne 0 si pas d'input trouvé.
    """
    try:
        # Cherche le champ de commentaire
        box = driver.find_elements(By.ID, "co.hinge.app:id/comment_composition_view")
        if box:
            text = box[0].text or ""
            return len(text)
        
        # Fallback sur EditText générique
        edit_fields = driver.find_elements(By.XPATH, "//android.widget.EditText")
        for field in edit_fields:
            try:
                if field.is_displayed():
                    text = field.text or ""
                    return len(text)
            except:
                pass
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("get_input_text_length")
    return 0

def is_input_too_long():
    """
    Détecte si le texte dans l'input dépasse la limite autorisée.
    """
    length = get_input_text_length()
    if length > MAX_COMMENT_LENGTH:
        print(f"⚠️ Input trop long ({length} chars > {MAX_COMMENT_LENGTH})")
        return True
    return False

def clear_input_field():
    """
    Vide le champ de texte de l'input.
    Retourne True si réussi.
    """
    try:
        # Cherche le champ de commentaire
        box = driver.find_elements(By.ID, "co.hinge.app:id/comment_composition_view")
        if box:
            try:
                box[0].clear()
                time.sleep(0.2)
                return True
            except:
                pass
        
        # Fallback sur EditText générique
        edit_fields = driver.find_elements(By.XPATH, "//android.widget.EditText")
        for field in edit_fields:
            try:
                if field.is_displayed():
                    field.clear()
                    time.sleep(0.2)
                    return True
            except:
                pass
                
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("clear_input_field")
    return False

def clear_input_and_recover():
    """
    Nettoie l'état bugué : efface l'input, ferme l'overlay, et revient
    à un état propre pour trouver le bouton Like.
    Retourne True si la récupération a réussi.
    """
    print("🔧 Détection état bugué — tentative de récupération...")
    
    try:
        # 1) Essayer de cliquer sur Cancel directement
        cancel_btns = driver.find_elements(By.XPATH,
            "//*[@text='Cancel' or @content-desc='Cancel']"
        )
        if cancel_btns:
            try:
                cancel_btns[0].click()
                time.sleep(0.5)
                print("✅ Cliqué sur Cancel")
                return True
            except Exception as e:
                if is_uia2_crash_msg(str(e)):
                    recover_session("clear_input.cancel_click")
                    return False
        
        # 2) Essayer de vider le champ texte et fermer
        edit_fields = driver.find_elements(By.XPATH, "//android.widget.EditText")
        for field in edit_fields:
            try:
                if field.is_displayed():
                    field.clear()
                    time.sleep(0.2)
            except:
                pass
        
        # 3) Fermer l'overlay via back ou bouton close
        close_overlay_if_open()
        time.sleep(0.3)
        
        # 4) Vérifier si on est sorti de l'état bugué
        if not is_buggy_overlay_state() and not is_comment_overlay_open():
            print("✅ État récupéré après fermeture overlay")
            return True
        
        # 5) Dernier recours : plusieurs back() avec vérification foreground
        for i in range(3):
            still_fg = _safe_back()
            if not still_fg:
                print(f"⚠️ back() #{i+1} a réduit Hinge — arrêt des retours")
                return False
            if not is_buggy_overlay_state():
                print(f"✅ État récupéré après {i+1} back()")
                return True
        
        print("⚠️ Impossible de récupérer — on swipe au profil suivant")
        return False
        
    except Exception as e:
        print(f"[clear_input_and_recover] error: {e}")
        if is_uia2_crash_msg(str(e)):
            recover_session("clear_input_and_recover.general")
        return False

def is_valid_prompt_text(prompt_text: str) -> bool:
    """
    Vérifie si le texte de prompt détecté est valide ou si c'est
    un faux positif (overlay bugué, éléments UI, pronoms, nom seul, etc.)
    """
    if not prompt_text:
        return False

    # Patterns invalides (éléments UI, pas de vrais prompts)
    invalid_patterns = [
        "cancel",
        "send priority",
        "send like",
        "priority like",
        "close",
        "back",
        "navigate up",
    ]

    prompt_lower = prompt_text.lower()

    # Si le prompt contient UNIQUEMENT ces mots UI, c'est invalide
    for pattern in invalid_patterns:
        if pattern in prompt_lower and len(prompt_text.strip()) < 50:
            return False

    # Si "Hinge Prompt: Cancel" c'est clairement bugué
    if "hinge prompt: cancel" in prompt_lower:
        return False

    # Extraire question et réponse nettoyées
    clean_text = prompt_text.replace("Hinge Prompt:", "").replace("User Answer:", "").strip()

    # Trop court = invalide
    if len(clean_text) < 10:
        return False

    # Patterns de pronoms seuls (faux positifs fréquents : "she/her", "he/him", etc.)
    pronoun_patterns = [
        r"^(she|he|they|ze|xe|ve|ey)[/\|](her|him|them|hir|zir|xem|ver|em)s?(/\w+)?$",
        r"^(she|he|they)/\w+/?\w*$",
    ]
    for pat in pronoun_patterns:
        if re.search(pat, clean_text.strip().lower()):
            return False

    # Si la réponse (User Answer) ne contient que des pronoms, c'est invalide
    lines = prompt_text.split("\n")
    answer_line = ""
    question_line = ""
    for line in lines:
        if "User Answer:" in line:
            answer_line = line.replace("User Answer:", "").strip()
        elif "Hinge Prompt:" in line:
            question_line = line.replace("Hinge Prompt:", "").strip()

    if answer_line:
        answer_lower = answer_line.lower()
        # Réponse qui ressemble à des pronoms
        for pat in pronoun_patterns:
            if re.search(pat, answer_lower):
                return False
        # Réponse très courte (≤4 mots) et question très courte (≤3 mots) → probablement nom + pronoms
        if (len(answer_line.split()) <= 4 and
                len(question_line.split()) <= 3 and
                len(clean_text) < 40):
            return False

    return True


def _xml_attr_in_tag(chunk: str, name: str):
    m = re.search(rf'\b{name}="([^"]*)"', chunk)
    if not m:
        return None
    s = m.group(1)
    s = (
        s.replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )
    return s


def _first_text_for_resource_id(xml: str, res_id: str) -> str:
    """Lit text= sur le tag qui contient resource-id (fallback quand find_elements ne voit pas les nœuds)."""
    needle = f'resource-id="{res_id}"'
    start_search = 0
    while True:
        pos = xml.find(needle, start_search)
        if pos == -1:
            return ""
        lt = xml.rfind("<", 0, pos)
        if lt < 0:
            start_search = pos + 1
            continue
        gt = xml.find(">", pos)
        if gt < 0:
            return ""
        chunk = xml[lt : gt + 1]
        text = _xml_attr_in_tag(chunk, "text")
        if text and text.strip():
            return text.strip()
        desc = _xml_attr_in_tag(chunk, "content-desc")
        if desc and desc.strip():
            return desc.strip()
        start_search = pos + 1


def _decode_xml_attr_value(s: str) -> str:
    if not s:
        return ""
    return (
        s.replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&apos;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )


def _expand_attr_value_chunks(v: str) -> list[str]:
    """Découpe les gros content-desc multi-lignes (prompt + réponse dans un seul attribut)."""
    v = v.strip()
    if not v:
        return []
    if "\n" in v and len(v) > 20:
        parts = [p.strip() for p in re.split(r"\n+", v) if p.strip()]
        if len(parts) > 1:
            return parts
    return [v]


def _page_source_attr_strings_in_order(xml: str) -> list[str]:
    """text= et content-desc= dans l'ordre du XML, dédupliqués (insensible à la casse)."""
    out: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r'\b(text|content-desc)="([^"]*)"', xml):
        raw = m.group(2)
        for chunk in _expand_attr_value_chunks(_decode_xml_attr_value(raw)):
            if not chunk:
                continue
            k = chunk.casefold()
            if k in seen:
                continue
            seen.add(k)
            out.append(chunk)
    return out


def _is_page_source_noise_string(s: str) -> bool:
    sl = s.strip().casefold()
    if sl in _PAGE_SOURCE_UI_EXACT:
        return True
    if sl.isdigit():
        return True
    if len(sl) <= 1:
        return True
    return False


def _looks_like_hinge_prompt_header(s: str) -> bool:
    sl = s.lower()
    if any(marker in sl for marker in _PROMPT_HEADER_MARKERS):
        return True
    if "?" in s and 10 <= len(s) <= 160:
        return True
    return False


def _next_substantive_after_prompt(cands: list[str], prompt_idx: int) -> str:
    for j in range(prompt_idx + 1, min(prompt_idx + 12, len(cands))):
        cand = cands[j]
        if _is_page_source_noise_string(cand):
            continue
        if len(cand.split()) == 1 and cand.strip().casefold() in _PAGE_SOURCE_SKIP_ONE_WORD_ANSWER:
            continue
        cl = cand.lower()
        if any(marker in cl for marker in _PROMPT_HEADER_MARKERS) and len(cand) < 120:
            continue
        if _looks_like_hinge_prompt_header(cand) and len(cand) < 120 and "?" in cand:
            continue
        return cand
    return ""


def _prompt_pair_from_page_source_attrs(xml: str):
    """
    Retourne (question, answer) si le XML expose le prompt en text/content-desc
    (cas fréquent avec Compose où les TextView du prompt ne sont pas listés).
    """
    cands = _page_source_attr_strings_in_order(xml)
    if len(cands) < 2:
        return None
    for i, q in enumerate(cands):
        if not _looks_like_hinge_prompt_header(q):
            continue
        a = _next_substantive_after_prompt(cands, i)
        if not a:
            continue
        full = f"Hinge Prompt: {q}\nUser Answer: {a}"
        if is_valid_prompt_text(full):
            return (q, a)
    return None


def find_prompt_text():
    """Retourne (has_prompt, text, reply_queue_limited) pour le profil courant."""

    # 0) D'abord vérifier si on est dans un état bugué
    if is_buggy_overlay_state():
        return False, "", False

    if detect_hinge_reply_queue_limit():
        return False, "", True

    # 1) Cas structure connue (prompt_bubble via ID)
    try:
        bubbles = driver.find_elements(By.ID, "co.hinge.app:id/prompt_bubble")
        if bubbles:
            b = bubbles[0]
            q = ""
            a = ""
            try:
                q = b.find_element(By.ID, "co.hinge.app:id/question").text.strip()
            except Exception:
                pass
            try:
                a = b.find_element(By.ID, "co.hinge.app:id/answer").text.strip()
            except Exception:
                pass
            
            full = f"Hinge Prompt: {q}\nUser Answer: {a}".strip()
            
            # Validation du prompt
            if (q or a) and is_valid_prompt_text(full):
                return True, full, False
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("find_prompt_text.step1")

    # 1b) Mêmes resource-id via page_source (souvent présent même si UiAutomator ne renvoie pas les widgets)
    try:
        raw = driver.page_source or ""
        if raw:
            q = _first_text_for_resource_id(raw, "co.hinge.app:id/question")
            a = _first_text_for_resource_id(raw, "co.hinge.app:id/answer")
            full = f"Hinge Prompt: {q}\nUser Answer: {a}".strip()
            if (q or a) and is_valid_prompt_text(full):
                return True, full, False
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("find_prompt_text.page_source_ids")

    # 1c) Compose / WebView : texte et content-desc dans le XML (souvent absents des TextView pour le bloc prompt)
    try:
        raw = driver.page_source or ""
        if raw:
            paired = _prompt_pair_from_page_source_attrs(raw)
            if paired:
                q, a = paired
                full = f"Hinge Prompt: {q}\nUser Answer: {a}".strip()
                if is_valid_prompt_text(full):
                    return True, full, False
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("find_prompt_text.page_source_attrs")

    # 2) Cas heuristique (via texte brut)
    try:
        tvs = driver.find_elements(By.XPATH, "//android.widget.TextView")
        texts = [t.text.strip() for t in tvs if t.text and t.text.strip()]

        # Filtrer les textes UI invalides
        filtered_texts = []
        ui_words = ["cancel", "close", "back", "send", "priority", "like", "navigate"]
        for t in texts:
            t_lower = t.lower()
            # Garder seulement les textes qui ne sont pas des éléments UI
            if not any(ui_word == t_lower for ui_word in ui_words):
                filtered_texts.append(t)

        known_headers = [
            "?",
            "together, we could",
            "i get along",
            "green flags",
            "my simple pleasures",
            "dating me is like",
            "i want someone who",
            "the way to win me over",
            "my most irrational fear",
            "we'll get along if",
            "i go crazy",
            "go crazy for",
        ]

        for i, t in enumerate(filtered_texts):
            t_lower = t.lower()
            if any(k in t_lower for k in known_headers):
                answer_text = ""
                if i + 1 < len(filtered_texts):
                    answer_text = filtered_texts[i+1]
                
                full_text = f"Hinge Prompt: {t}\nUser Answer: {answer_text}".strip()
                
                if is_valid_prompt_text(full_text):
                    return True, full_text, False

        # Fallback générique
        if len(filtered_texts) >= 2:
            full_text = f"Hinge Prompt: {filtered_texts[0]}\nUser Answer: {filtered_texts[1]}"
            if is_valid_prompt_text(full_text):
                return True, full_text, False
        elif filtered_texts:
            if is_valid_prompt_text(filtered_texts[0]):
                return True, filtered_texts[0], False

    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("find_prompt_text.step2")

    return False, "", False

# ---------- LOCATOR unique du coeur par profil ----------
def choose_like_locator_for_profile():
    """
    Priorité : coeur dans le prompt s'il existe, sinon coeur principal.
    """
    try:
        if driver.find_elements(By.ID, "co.hinge.app:id/prompt_bubble"):
            xpath_in_prompt = (
                "(//android.view.ViewGroup[@resource-id='co.hinge.app:id/prompt_bubble'])[1]"
                "//*[(@content-desc='Like' or contains(@content-desc,'Like') or contains(@content-desc,'heart'))"
                " and not(contains(translate(@content-desc,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'send'))]"
            )
            if driver.find_elements(By.XPATH, xpath_in_prompt):
                return ('xpath', xpath_in_prompt)
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("choose_like_locator")

    return ('accessibility_id', 'Like')

def resolve_and_click(locator, must_not_open_overlay=False):
    """
    Résout le locator -> WebElement frais -> clic.
    """
    strategy, value = locator
    try:
        if strategy == 'accessibility_id':
            el = wait_short.until(EC.element_to_be_clickable((AppiumBy.ACCESSIBILITY_ID, value)))
        else:
            el = wait_short.until(EC.element_to_be_clickable((By.XPATH, value)))
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("resolve_and_click.until")
            return False
        if strategy == 'accessibility_id':
            try:
                alt = "//*[contains(@content-desc,'Like') and not(contains(translate(@content-desc,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'send'))]"
                el = wait_short.until(EC.element_to_be_clickable((By.XPATH, alt)))
            except Exception as ee:
                if is_uia2_crash_msg(str(ee)):
                    recover_session("resolve_and_click.fallback")
                return False
        else:
            return False

    try:
        el.click()
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("resolve_and_click.click")
            return False
        try:
            bounds = el.get_attribute("bounds")
            x1, y1, x2, y2 = map(int, re.findall(r"\d+", bounds))
            cx, cy = (x1 + x2)//2, (y1 + y2)//2
            driver.execute_script("mobile: clickGesture", {"x": cx, "y": cy})
        except Exception as ee:
            if is_uia2_crash_msg(str(ee)):
                recover_session("resolve_and_click.bounds")
            return False

    if must_not_open_overlay:
        time.sleep(0.25)
        if is_comment_overlay_open():
            close_overlay_if_open()
            return False
    return True

# ---------- Envoi Priority Like ----------
def _click_send_like_without_comment():
    """
    Clique sur 'Send like' (sans commentaire) ou ferme l'overlay et fait un like direct.
    Utilisé quand l'input est trop long ou bugué.
    """
    try:
        # Option 1: Chercher un bouton "Send like" simple (pas priority)
        send_btns = driver.find_elements(By.XPATH,
            "//*[@content-desc='Send like' or @text='Send like']"
        )
        if send_btns:
            send_btns[0].click()
            time.sleep(0.25)
            return True
        
        # Option 2: Chercher "Send priority like" et cliquer quand même
        # (si l'input est vide, ça devrait marcher)
        priority_btns = driver.find_elements(By.XPATH,
            "//*[@content-desc='Send priority like' or @text='Send priority like']"
        )
        if priority_btns:
            priority_btns[0].click()
            time.sleep(0.25)
            return True
        
        # Option 3: Fallback - fermer l'overlay et ne rien envoyer
        # Le caller fera un like direct après
        return False
        
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("send_like_without_comment")
        return False

def _try_hide_keyboard():
    """Réduit les échecs de locateur quand le clavier couvre le bouton d’envoi."""
    try:
        if getattr(driver, "is_keyboard_shown", None) and driver.is_keyboard_shown():
            driver.hide_keyboard()
            time.sleep(0.28)
            return
    except Exception:
        pass
    try:
        driver.hide_keyboard()
        time.sleep(0.28)
    except Exception:
        pass


def _priority_like_overlay_dismissed(wait_sec: float = 2.0) -> bool:
    """True si l’overlay commentaire / envoi ne semble plus ouverte (succès probable)."""
    end = time.time() + wait_sec
    while time.time() < end:
        try:
            if not is_comment_overlay_open():
                return True
        except Exception as e:
            if is_uia2_crash_msg(str(e)):
                raise
        time.sleep(0.12)
    return False


def _tap_rect_center_click_gesture(rect) -> bool:
    if not rect:
        return False
    try:
        l, t, r, b = rect
        x, y = (l + r) // 2, (t + b) // 2
        driver.execute_script("mobile: clickGesture", {"x": int(x), "y": int(y)})
        time.sleep(0.3)
        return True
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            raise
        return False


def _click_send_priority_element_or_bounds(el) -> bool:
    """Compose : préférer tap sur bounds; sinon click() classique."""
    try:
        raw = el.get_attribute("bounds")
        rect = _parse_bounds_string(raw)
        if rect:
            l, t, r, b = rect
            if (r - l) >= 40 and (b - t) >= 20:
                if _tap_rect_center_click_gesture(rect) and _priority_like_overlay_dismissed(1.8):
                    return True
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            raise
    try:
        el.click()
        time.sleep(0.3)
        return _priority_like_overlay_dismissed(2.0)
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            raise
        return False


def _click_send_priority_like_via_candidate_elements() -> bool:
    locators = (
        (AppiumBy.ACCESSIBILITY_ID, "Send priority like"),
        (By.XPATH, "//*[@content-desc='Send priority like']"),
        (By.XPATH, "//*[contains(@content-desc,'Send priority like')]"),
        (By.XPATH, "//*[contains(@content-desc,'Priority like')]"),
        (
            By.XPATH,
            "//*[contains(translate(@content-desc,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'priority like')]",
        ),
        (By.XPATH, "//*[@text='Send priority like']"),
        (By.XPATH, "//*[contains(@text,'Send priority like')]"),
        (
            By.XPATH,
            "//*[contains(translate(@text,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'send priority')]",
        ),
        (
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().descriptionContains("Send priority like")',
        ),
        (
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().descriptionContains("priority like")',
        ),
        (
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().textContains("Send priority like")',
        ),
    )
    for by, sel in locators:
        try:
            for el in driver.find_elements(by, sel):
                try:
                    if not el.is_displayed():
                        continue
                except Exception:
                    continue
                if _click_send_priority_element_or_bounds(el):
                    return True
        except Exception as e:
            if is_uia2_crash_msg(str(e)):
                recover_session("priority_like.candidates")
                return False
    return False


def _click_send_priority_like_via_page_source_rect() -> bool:
    rect = _find_send_priority_like_rect_from_page_source()
    if not rect:
        return False
    if _tap_rect_center_click_gesture(rect):
        return _priority_like_overlay_dismissed(2.0)
    return False


def _click_send_priority_like_via_percent_taps() -> bool:
    try:
        sz = _safe_get_window_size()
        w, h = sz["width"], sz["height"]
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            raise
        return False
    for xr, yr in (
        (0.55, 0.72),
        (0.62, 0.73),
        (0.50, 0.78),
        (0.72, 0.68),
        (0.48, 0.65),
        (0.58, 0.66),
    ):
        try:
            driver.execute_script(
                "mobile: clickGesture",
                {"x": int(w * xr), "y": int(h * yr)},
            )
            time.sleep(0.28)
            if _priority_like_overlay_dismissed(1.0):
                return True
        except Exception as e:
            if is_uia2_crash_msg(str(e)):
                recover_session("priority_like.coords")
                return False
    return False


def _click_send_priority_like():
    """
    Envoie le priority like. Le clavier logiciel casse souvent les locateurs : on le replie d’abord.
    On vérifie que l’overlay se ferme plutôt que de retourner True après un tap aveugle.
    """
    for round_i in range(2):
        print(f"🔍 _click_send_priority_like round {round_i}")
        time.sleep(0.22 if round_i == 0 else 0.35)

        if _click_send_priority_like_via_candidate_elements():
            return True

        try:
            btn = wait_short.until(
                EC.element_to_be_clickable(
                    (AppiumBy.ACCESSIBILITY_ID, "Send priority like")
                )
            )
            if _click_send_priority_element_or_bounds(btn):
                return True
        except Exception as e:
            if is_uia2_crash_msg(str(e)):
                recover_session("priority_like.access_id")
                return False

        try:
            btn = wait_short.until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//*[@content-desc='Send priority like']")
                )
            )
            if _click_send_priority_element_or_bounds(btn):
                return True
        except Exception as e:
            if is_uia2_crash_msg(str(e)):
                recover_session("priority_like.xpath")
                return False

        if _click_send_priority_like_via_page_source_rect():
            return True

        if _click_send_priority_like_via_percent_taps():
            return True

        time.sleep(0.4)

    return False

# ---------- Like direct ----------

def _confirm_like_in_overlay():
    """
    Après avoir ouvert l'overlay de like, clique sur "Send like" pour confirmer
    sans commentaire. Retourne True si réussi.
    Sur Hinge, cliquer le coeur ouvre TOUJOURS une overlay — il faut confirmer.
    """
    # Priorité : "Send like" simple (sans priority)
    for locator in [
        (AppiumBy.ACCESSIBILITY_ID, "Send like"),
        (By.XPATH, "//*[@content-desc='Send like' or @text='Send like']"),
        (By.XPATH, "//*[contains(@content-desc,'Send like') and not(contains(@content-desc,'priority'))]"),
        (By.XPATH, "//*[contains(@text,'Send like') and not(contains(@text,'priority'))]"),
    ]:
        try:
            btn = wait_short.until(EC.element_to_be_clickable(locator))
            btn.click()
            time.sleep(0.4)
            print("✅ 'Send like' confirmé via overlay")
            return True
        except Exception as e:
            if is_uia2_crash_msg(str(e)):
                recover_session("confirm_like.send_like")
                return False

    # Fallback : "Send priority like" si c'est la seule option
    try:
        btn = wait_short.until(
            EC.element_to_be_clickable((AppiumBy.ACCESSIBILITY_ID, "Send priority like"))
        )
        btn.click()
        time.sleep(0.4)
        print("✅ 'Send priority like' confirmé via overlay (fallback)")
        return True
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("confirm_like.priority")
            return False

    try:
        sz = _safe_get_window_size()
        w, h = sz["width"], sz["height"]
        for xr, yr in ((0.52, 0.76), (0.52, 0.84), (0.62, 0.72)):
            driver.execute_script("mobile: clickGesture", {"x": int(w * xr), "y": int(h * yr)})
            time.sleep(0.45)
            if not is_comment_overlay_open() and not is_buggy_overlay_state():
                print("✅ Like confirmé (tap approximatif après échec des locateurs)")
                return True
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("confirm_like.coord_fallback")
            return False

    return False

def _tap_like_by_coords_and_confirm():
    """
    Dernier recours : tape aux coordonnées du bouton Like puis confirme l'overlay.
    """
    try:
        sz = _safe_get_window_size()
        w, h = sz["width"], sz["height"]
        x = int(w * 0.80)
        y = int(h * 0.88)
        driver.execute_script("mobile: clickGesture", {"x": x, "y": y})
        time.sleep(0.9)
        print(f"📍 Tap par coordonnées ({x},{y})")
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("tap_by_coords")
        return False

    _wait_for_like_overlay_hint(2.0)
    if is_comment_overlay_open() or is_buggy_overlay_state() or _page_source_suggests_like_overlay():
        return _confirm_like_in_overlay()

    print(
        "⚠️ Tap coordonnées : aucune overlay d’envoi détectée — le like n’est probablement pas parti."
    )
    return False

def like_direct_no_overlay(like_locator):
    """
    Like sans commentaire : clique le bouton Like et confirme l'overlay si nécessaire.
    Sur Hinge, le coeur ouvre TOUJOURS une overlay — on confirme avec 'Send like'.
    """
    # Nettoyer un overlay déjà ouvert (état bugué)
    if is_comment_overlay_open() or is_buggy_overlay_state():
        close_overlay_if_open()
        time.sleep(0.3)

    # Cliquer sur le bouton Like (autoriser l'overlay)
    ok = resolve_and_click(like_locator, must_not_open_overlay=False)
    if not ok:
        # Fallback par coordonnées + confirmation overlay
        return _tap_like_by_coords_and_confirm()

    # Attendre plus longtemps — l'overlay Hinge peut mettre jusqu'à ~0.8s à apparaître
    time.sleep(0.8)

    if detect_hinge_reply_queue_limit():
        print(
            "⛔ Like direct impossible pour l'instant : file de réponses saturée sur Hinge. "
            "Réponds à des matchs puis relance."
        )
        close_overlay_if_open()
        return False

    _wait_for_like_overlay_hint(2.8)

    if detect_hinge_reply_queue_limit():
        print(
            "⛔ Like direct impossible pour l'instant : file de réponses saturée sur Hinge. "
            "Réponds à des matchs puis relance."
        )
        close_overlay_if_open()
        return False

    if is_comment_overlay_open() or is_buggy_overlay_state() or _page_source_suggests_like_overlay():
        confirmed = _confirm_like_in_overlay()
        if not confirmed:
            time.sleep(0.45)
            confirmed = _confirm_like_in_overlay()
        if not confirmed:
            close_overlay_if_open()
            print(
                "⚠️ Like non confirmé : « Send like » introuvable ou overlay illisible pour UiAutomator."
            )
            return False
        return True

    print(
        "⚠️ Like non confirmé : pas d’overlay d’envoi après le cœur "
        "(Hinge exige en général un second tap ; l’UI n’a peut‑être pas été exposée au driver)."
    )
    return False

# ---------- Comment + Priority Like ----------
def _first_displayed(els):
    for e in els or []:
        try:
            if e.is_displayed():
                return e
        except Exception:
            pass
    return None


def _parse_bounds_attr(tag: str):
    """Extrait [left,top][right,bottom] depuis un fragment de balise hierarchy Android."""
    m = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', tag)
    if not m:
        return None
    return tuple(map(int, m.groups()))


def _parse_bounds_string(bounds_str: str):
    """Parse l'attribut bounds d'un WebElement Android : '[l,t][r,b]'."""
    if not bounds_str:
        return None
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str.strip())
    if not m:
        return None
    return tuple(map(int, m.groups()))


def _find_send_priority_like_rect_from_page_source():
    """Bounds (l,t,r,b) du bouton « Send priority like » depuis page_source, ou None."""
    if not (_page_source_suggests_like_overlay() or is_comment_overlay_open()):
        return None
    try:
        src = driver.page_source or ""
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("send_priority.rect.page_source")
        return None
    if len(src) < 200:
        return None
    send_rects = []
    for m in re.finditer(r"<([^>]+)>", src):
        tag = m.group(1)
        tl = tag.lower()
        if "send priority like" not in tl:
            continue
        rect = _parse_bounds_attr(tag)
        if rect:
            send_rects.append(rect)
    try:
        sz = _safe_get_window_size()
        W, H = sz["width"], sz["height"]
    except Exception:
        W, H = 1080, 2400
    best_send = None
    for rect in send_rects:
        l, t, r, b = rect
        cy = (t + b) // 2
        if cy < H * 0.22 or cy > H * 0.92:
            continue
        wbtn = r - l
        if wbtn < 72:
            continue
        if best_send is None or wbtn > best_send[0]:
            best_send = (wbtn, rect)
    return best_send[1] if best_send else None


def _get_send_priority_button_bounds_from_ui():
    """
    Bounds du bouton « Send priority like » quand l’overlay est ouverte.
    Plus fiable que le page source seul (Compose).
    """
    locators = (
        (AppiumBy.ACCESSIBILITY_ID, "Send priority like"),
        (By.XPATH, "//*[@content-desc='Send priority like']"),
        (By.XPATH, "//*[contains(@content-desc,'Send priority like')]"),
    )
    for by, sel in locators:
        try:
            el = _first_displayed(driver.find_elements(by, sel))
            if not el:
                continue
            raw = el.get_attribute("bounds")
            rect = _parse_bounds_string(raw)
            if rect:
                return rect
        except Exception as e:
            if is_uia2_crash_msg(str(e)):
                recover_session("send_priority.bounds")
                return None
    return None


def _comment_tap_point_from_send_rect(l, t, r, b, W: int, H: int):
    """
    Le champ 'Add a comment' est AU-DESSUS de la rangee [Rose | Send Priority Like].
    On ancre verticalement au-dessus du haut du bouton, centre horizontalement.
    """
    sx1, sy1, sx2, sy2 = l, t, r, b
    btn_top = sy1
    btn_h = sy2 - sy1
    if btn_h < 20:
        return None
    wbtn = sx2 - sx1
    if wbtn < 72:
        return None
    comment_field_h = max(btn_h, 60)
    cy = btn_top - int(comment_field_h * 0.6)
    cy = max(int(H * 0.15), min(cy, btn_top - 20))
    cx = W // 2
    if cy < H * 0.10 or cy > H * 0.90:
        return None
    print(f"📐 comment tap from send rect: ({cx}, {cy})  [send btn top={btn_top}]")
    return (cx, cy)


def _resolve_comment_tap_coordinates():
    """
    Point (cx, cy) pour ouvrir / focus le champ : d’abord via le bouton Send (UI),
    sinon analyse du page source.
    """
    try:
        sz = _safe_get_window_size()
        W, H = sz["width"], sz["height"]
    except Exception:
        W, H = 1080, 2400

    rect = _get_send_priority_button_bounds_from_ui()
    if rect:
        print(f"📐 send button bounds (UI): {rect}")
        pt = _comment_tap_point_from_send_rect(*rect, W, H)
        if pt:
            return pt
    else:
        print("📐 send button NOT found via UI elements")

    return _find_comment_field_point_from_page_source()


def _paste_comment_via_clipboard(comment_text: str) -> bool:
    """Colle le texte complet via presse-papiers (évite adb input text qui casse UTF-8 / apostrophes)."""
    if not comment_text:
        return False
    try:
        b64 = base64.b64encode(comment_text.encode("utf-8")).decode("ascii")
        try:
            driver.execute_script(
                "mobile: setClipboard",
                {"content": b64, "contentType": "plaintext"},
            )
        except Exception:
            driver.execute_script(
                "mobile: setClipboard",
                {"content": comment_text, "contentType": "plaintext"},
            )
        time.sleep(0.1)
        driver.press_keycode(279)
        return True
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            raise
        return False


def _text_safe_for_adb_input_text(s: str) -> bool:
    """mobile:type utilise adb input text — unsafe pour la plupart des commentaires IA."""
    if not s:
        return True
    for ch in s:
        o = ord(ch)
        if ch in "\n\r\t":
            return False
        if o < 32 or o > 126:
            return False
        if ch in {'&', '$', '`', '"', "'", "\\"}:
            return False
    return True


def _find_comment_field_point_from_page_source():
    """
    Quand Compose n'expose pas d'EditText, le dump UiAutomator contient souvent quand même
    des bounds pour le champ (hint / content-desc) ou on peut ancrer depuis « Send priority like ».
    Retourne (cx, cy) ou None.
    """
    if not (_page_source_suggests_like_overlay() or is_comment_overlay_open()):
        return None
    try:
        src = driver.page_source or ""
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            recover_session("comment_point.page_source")
        return None
    if len(src) < 200:
        return None

    def _center(rect):
        l, t, r, b = rect
        return (l + r) // 2, (t + b) // 2

    candidates = []

    for m in re.finditer(r"<([^>]+)>", src):
        tag = m.group(1)
        tl = tag.lower()
        if "send priority like" in tl:
            continue
        if "send like" in tl and "priority" not in tl:
            continue
        rect = _parse_bounds_attr(tag)
        if not rect:
            continue
        l, t, r, b = rect
        w, h = r - l, b - t
        if w < 40 or h < 18 or w > 1900:
            continue

        score = 0
        if "add a comment" in tl:
            score += 5000
        elif "add comment" in tl and "send" not in tl:
            score += 4000
        elif "ajouter" in tl and "comment" in tl:
            score += 4000
        elif "hint" in tl and "comment" in tl:
            score += 3500
        elif "content-desc" in tl and "comment" in tl and "send" not in tl and "priority" not in tl:
            score += 3000
        elif "comment_composition" in tl or "commentcomposition" in tl.replace("_", ""):
            score += 2800
        elif "resource-id" in tl and "comment" in tl and "send" not in tl:
            score += 2200

        if score == 0:
            continue
        if 'focusable="true"' in tl:
            score += 800
        if 'clickable="true"' in tl:
            score += 400
        if 'password="true"' in tl:
            score -= 2000
        candidates.append((score, w * h, _center(rect)))

    if candidates:
        candidates.sort(key=lambda x: (-x[0], -x[1]))
        return candidates[0][2]

    anchored = _find_send_priority_like_rect_from_page_source()
    if anchored:
        try:
            sz = _safe_get_window_size()
            W, H = sz["width"], sz["height"]
        except Exception:
            W, H = 1080, 2400
        pt = _comment_tap_point_from_send_rect(*anchored, W, H)
        if pt:
            return pt

    return None


def _try_type_comment_at_coordinates(cx: int, cy: int, comment_text: str) -> bool:
    """Focus par tap aux coordonnées puis collage (évite mobile:type / adb input text qui corrompt l’UTF-8)."""
    if not comment_text:
        return False
    print(f"📐 tapping comment field at ({cx}, {cy})")
    for _ in range(2):
        try:
            driver.execute_script("mobile: clickGesture", {"x": int(cx), "y": int(cy)})
        except Exception as e:
            if is_uia2_crash_msg(str(e)):
                raise
            return False
        time.sleep(0.32)
    if _paste_comment_via_clipboard(comment_text):
        time.sleep(0.2)
        return True
    if _text_safe_for_adb_input_text(comment_text):
        try:
            driver.execute_script("mobile: type", {"text": comment_text})
            return True
        except Exception as e:
            if is_uia2_crash_msg(str(e)):
                raise
    return False


def _find_comment_input_element():
    """
    Une passe : repère un élément pour saisir le commentaire.
    Sur builds Compose, le champ n'est souvent ni comment_composition_view ni EditText classique.
    """
    try:
        p = _first_displayed(
            driver.find_elements(By.ID, "co.hinge.app:id/comment_composition_view")
        )
        if p:
            return p

        for acc in (
            "Add a comment",
            "Add comment",
            "Comment",
            "Add a reply",
            "Ajouter un commentaire",
            "Ajoute un commentaire",
        ):
            p = _first_displayed(driver.find_elements(AppiumBy.ACCESSIBILITY_ID, acc))
            if p:
                return p

        xpaths = (
            "//*[@content-desc='Add a comment']",
            "//*[contains(@content-desc,'Add a comment')]",
            "//*[contains(translate(@content-desc,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'add a comment')]",
            "//*[contains(@hint,'Add a comment')]",
            "//*[contains(translate(@hint,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'add a comment')]",
            "//*[contains(translate(@content-desc,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'add') and "
            "contains(translate(@content-desc,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'comment')]",
            "//*[contains(@resource-id,'comment_composition')]",
            "//*[contains(@resource-id,'comment') and contains(@resource-id,'composition')]",
        )
        for xp in xpaths:
            try:
                p = _first_displayed(driver.find_elements(By.XPATH, xp))
                if p:
                    return p
            except Exception:
                pass

        uia_selectors = (
            'new UiSelector().descriptionContains("Add a comment")',
            'new UiSelector().descriptionContains("Add comment")',
            'new UiSelector().textContains("Add a comment")',
            'new UiSelector().descriptionContains("Ajouter un commentaire")',
            'new UiSelector().resourceId("co.hinge.app:id/comment_composition_view")',
            'new UiSelector().className("android.widget.EditText").focused(true)',
            'new UiSelector().className("android.widget.AutoCompleteTextView")',
        )
        for sel in uia_selectors:
            try:
                p = _first_displayed(
                    driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, sel)
                )
                if p:
                    return p
            except Exception:
                pass

        for e in driver.find_elements(By.XPATH, "//android.widget.EditText"):
            try:
                if e.is_displayed():
                    return e
            except Exception:
                pass
    except Exception as ex:
        if is_uia2_crash_msg(str(ex)):
            recover_session("find_comment_input")
    return None


def _try_type_comment(box, comment_text: str) -> bool:
    """
    Enchaîne send_keys, replaceElementValue, collage presse-papiers, puis mobile:type
    seulement si le texte est sûr pour adb input (ASCII restreint).
    """
    if not box or not comment_text:
        return False

    def _click_safe(el):
        try:
            el.click()
        except Exception:
            pass

    _click_safe(box)
    time.sleep(0.12)
    try:
        box.clear()
    except Exception:
        pass

    try:
        box.send_keys(comment_text)
        return True
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            raise

    try:
        driver.execute_script(
            "mobile: replaceElementValue",
            {"elementId": box.id, "value": comment_text},
        )
        return True
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            raise

    try:
        _click_safe(box)
        time.sleep(0.2)
        if _paste_comment_via_clipboard(comment_text):
            return True
    except Exception as e:
        if is_uia2_crash_msg(str(e)):
            raise

    if _text_safe_for_adb_input_text(comment_text):
        try:
            _click_safe(box)
            time.sleep(0.22)
            driver.execute_script("mobile: type", {"text": comment_text})
            return True
        except Exception as e:
            if is_uia2_crash_msg(str(e)):
                raise
    return False


def _wait_for_comment_box(timeout: float = 7.0):
    """
    Attend l'apparition du champ de commentaire dans l'overlay.
    Retourne (web_element, None) ou (None, (cx, cy)) pour saisie par coordonnées (Compose).
    Retourne (None, None) si échec.
    """
    end = time.time() + timeout
    while time.time() < end:
        try:
            box = _find_comment_input_element()
            if box is not None:
                return box, None
            pt = _resolve_comment_tap_coordinates()
            if pt is not None:
                return None, pt
        except Exception as ex:
            if is_uia2_crash_msg(str(ex)):
                recover_session("wait_for_comment_box")
                return None, None
        time.sleep(0.28)
    return None, None


def like_with_comment(prompt_text: str, like_locator):
    """
    Ouvre l'overlay via le locator, commente, envoie 'Send priority like'.
    Si l'input est trop long ou bugué → vide l'input et fait un like direct.

    Retourne (ok, advanced, comment_sent) :
    - ok : like / overlay traité avec succès (y compris like sans texte).
    - advanced : profil prêt / swipe comme wait_profile_ready.
    - comment_sent : True seulement si le texte IA a été saisi puis « Send priority like ».
      False si like envoyé sans champ (Compose), chemin _confirm_like_in_overlay rapide, etc.
    """
    try:
        if not resolve_and_click(like_locator, must_not_open_overlay=False):
            return False, False, False

        # Attendre que l'overlay s'ouvre (Compose peut être lent à exposer la hiérarchie)
        time.sleep(1.0)

        if detect_hinge_reply_queue_limit():
            print(
                "⛔ Hinge bloque l'envoi (file de réponses saturée — ex. « You're over the limit »). "
                "Réponds à des matchs puis relance."
            )
            close_overlay_if_open()
            return False, False, False

        # Vérifier si l'overlay est réellement ouvert
        if not is_comment_overlay_open() and not is_buggy_overlay_state():
            _wait_for_like_overlay_hint(2.0)
            if is_comment_overlay_open() or is_buggy_overlay_state() or _page_source_suggests_like_overlay():
                if _confirm_like_in_overlay():
                    return True, wait_profile_ready(timeout_sec=3.0), False
            print("⚠️ Overlay commentaire non détectée après clic cœur — le script tentera un like direct.")
            return False, False, False

        # Vérifier si l'input est déjà trop long (état bugué précédent)
        if is_input_too_long():
            print("🔧 Input déjà trop long — nettoyage et like direct")
            clear_input_field()
            time.sleep(0.2)
            if _click_send_like_without_comment():
                return True, wait_profile_ready(timeout_sec=3.0), False
            else:
                close_overlay_if_open()
                return False, False, False

        comment_text = get_ai_comment(prompt_text)
        if not comment_text:
            close_overlay_if_open()
            return False, False, False

        # Vérifier que le commentaire généré n'est pas trop long
        if len(comment_text) > MAX_COMMENT_LENGTH:
            print(f"⚠️ Commentaire IA trop long ({len(comment_text)} chars) — like direct")
            if _click_send_like_without_comment():
                return True, wait_profile_ready(timeout_sec=3.0), False
            else:
                close_overlay_if_open()
                return False, False, False

        # Chercher le champ de saisie (élément ou point dérivé du page source)
        box, coord_point = _wait_for_comment_box(timeout=7.0)
        if box is None and coord_point is None:
            print("⚠️ Champ commentaire introuvable — like direct")
            if _click_send_like_without_comment():
                return True, wait_profile_ready(timeout_sec=3.0), False
            close_overlay_if_open()
            return False, False, False

        typed = False
        try:
            if box is not None:
                typed = _try_type_comment(box, comment_text)
                if not typed:
                    try:
                        box2 = wait.until(
                            EC.presence_of_element_located((By.XPATH, "//android.widget.EditText"))
                        )
                        typed = _try_type_comment(box2, comment_text)
                    except Exception as ee:
                        if is_uia2_crash_msg(str(ee)):
                            recover_session("comment.locate_fallback")
            elif coord_point is not None:
                typed = _try_type_comment_at_coordinates(
                    coord_point[0], coord_point[1], comment_text
                )
                if not typed:
                    pt2 = _find_comment_field_point_from_page_source()
                    if pt2 and pt2 != coord_point:
                        typed = _try_type_comment_at_coordinates(
                            pt2[0], pt2[1], comment_text
                        )
                if not typed:
                    time.sleep(0.35)
                    box3 = _find_comment_input_element()
                    if box3 is not None:
                        typed = _try_type_comment(box3, comment_text)
        except Exception as e:
            if is_uia2_crash_msg(str(e)):
                recover_session("comment.type")
            typed = False

        if not typed:
            print(
                "⚠️ Impossible de saisir le commentaire (Compose / champ non standard) — like direct"
            )
            if _click_send_like_without_comment():
                return True, wait_profile_ready(timeout_sec=3.0), False
            close_overlay_if_open()
            return False, False, False

        time.sleep(0.25)
        
        # NOUVEAU : Vérifier APRÈS saisie si l'input est trop long
        if is_input_too_long():
            print("⚠️ Input trop long après saisie — nettoyage et like direct")
            clear_input_field()
            time.sleep(0.2)
            if _click_send_like_without_comment():
                return True, wait_profile_ready(timeout_sec=3.0), False
            else:
                close_overlay_if_open()
                return False, False, False

        if not _click_send_priority_like():
            try:
                driver.execute_script("mobile: performEditorAction", {"action": "done"})
            except Exception as e:
                if is_uia2_crash_msg(str(e)):
                    recover_session("editorAction.done")
            close_overlay_if_open()
            return False, False, False

        for _ in range(14):
            if not is_comment_overlay_open():
                break
            time.sleep(0.15)
        else:
            print(
                "⚠️ Overlay toujours visible après envoi — le priority like n’a probablement pas été validé."
            )
            close_overlay_if_open()
            return False, False, False

        time.sleep(0.25)
        advanced = wait_profile_ready(timeout_sec=3.0)
        return True, advanced, True

    except Exception as e:
        print(f"[like_with_comment] error: {e}")
        if is_uia2_crash_msg(str(e)):
            recover_session("like_with_comment.general")
        try:
            close_overlay_if_open()
        except Exception:
            pass
        return False, False, False

# ---------- helpers boucle principale ----------

def _safe_screenshot_on_failure(idx: int):
    """Prend un screenshot en cas d'échec pour debug — sauvegardé dans shots/fail_XXXX.png"""
    try:
        import os
        os.makedirs("shots", exist_ok=True)
        path = f"shots/fail_{idx:04d}_{int(time.time())}.png"
        driver.save_screenshot(path)
        print(f"📸 Screenshot d'échec sauvegardé : {path}")
    except Exception as e:
        print(f"[screenshot_on_failure] impossible: {e}")

def _hard_recovery(reason: str = ""):
    """
    Recovery agressive : ferme tous les overlays, force le retour à l'écran
    de swipe Hinge, et recrée la session Appium si nécessaire.
    """
    print(f"🔴 Hard recovery... ({reason})")
    # 1) Tenter de fermer tout overlay
    for _ in range(3):
        try:
            if is_comment_overlay_open() or is_buggy_overlay_state():
                close_overlay_if_open()
                time.sleep(0.3)
            else:
                break
        except Exception:
            break

    # 2) S'assurer que Hinge est au premier plan
    try:
        ensure_hinge_foreground()
        time.sleep(0.5)
    except Exception:
        pass

    # 3) Recréer la session seulement si un autre package est lu explicitement (évite recover sur driver silencieux)
    pkg = None
    try:
        pkg = driver.execute_script("mobile: getCurrentPackage")
    except Exception:
        try:
            pkg = driver.current_package
        except Exception:
            pkg = None
    if pkg and pkg != APP_PKG:
        recover_session(f"hard_recovery: {reason}")
        time.sleep(1.0)

    # 4) Vider l'état bugué résiduel
    try:
        if is_buggy_overlay_state():
            clear_input_and_recover()
    except Exception:
        pass

    wait_profile_ready(timeout_sec=5.0)
    print("✅ Hard recovery terminé")


# ---------- main loop ----------
print("▶️ Start infinite like runner…")
idx = 0
fails = 0
MAX_FAILS = 5           # Seuil avant hard recovery (plus bas = plus réactif)
MAX_HARD_RECOVERY = 3   # Après N hard recoveries consécutives sans succès → stop vraiment
hard_recovery_count = 0
_last_profile_snapshot = None  # Pour détecter si on boucle sur le même profil

try:
    while True:
        idx += 1
        print(f"\n🔄 Processing profile #{idx}")

        if detect_end_of_stack():
            print("⛔ Fin de pile détectée — stop.")
            break

        ensure_hinge_foreground()

        if detect_hinge_reply_queue_limit():
            print(
                "⛔ Écran / bannière « réponses en attente » détecté(e) — Hinge refuse de nouveaux likes. "
                "Réponds à des conversations puis relance le bot."
            )
            break

        # --- Vérifier si on est dans un état bugué AVANT tout ---
        if is_buggy_overlay_state():
            print("⚠️ État bugué détecté (overlay mal fermé)")
            if clear_input_and_recover():
                print("✅ Récupération réussie")
                time.sleep(0.5)
            else:
                print("❌ Récupération échouée — swipe au suivant")
                swipe_to_next()
                ensure_hinge_foreground()
                wait_profile_ready()
                continue

        wait_profile_ready()

        # --- Détecteur "bloqué sur le même profil" ---
        try:
            current_snapshot = " ".join([
                el.text for el in driver.find_elements(
                    By.XPATH, "//android.widget.TextView"
                ) if el.text
            ])[:200]
        except Exception:
            current_snapshot = ""

        if current_snapshot and current_snapshot == _last_profile_snapshot:
            print("⚠️ Même profil détecté deux fois — on force le swipe")
            _safe_screenshot_on_failure(idx)
            swipe_to_next()
            ensure_hinge_foreground()
            wait_profile_ready()
            _last_profile_snapshot = None
            fails += 1
        else:
            _last_profile_snapshot = current_snapshot

        # --- locator unique du coeur pour CE profil ---
        like_locator = choose_like_locator_for_profile()

        # --- texte prompt éventuel ---
        has_prompt, prompt_text, reply_queue_limited = find_prompt_text()
        if reply_queue_limited:
            print(
                "⛔ File de réponses saturée (détecté sur la carte) — Hinge refuse de nouveaux likes. "
                "Réponds à des conversations puis relance."
            )
            break

        # Double vérification : si le prompt semble invalide, traiter comme "pas de prompt"
        if has_prompt and not is_valid_prompt_text(prompt_text):
            print(f"⚠️ Prompt détecté mais invalide: {prompt_text[:50]}...")
            has_prompt = False
            prompt_text = ""

        if has_prompt and prompt_text:
            print(f"✅ Prompt trouvé : {prompt_text[:80]}...")
            ok, advanced, comment_sent = like_with_comment(prompt_text, like_locator)
            if ok:
                if comment_sent:
                    print("📝 Comment envoyé + priority like")
                    log_profile(idx, prompt_text, "comment+priority_like")
                else:
                    print(
                        "✅ Like / priority like envoyé sans texte saisi par le bot "
                        "(champ non visible pour Appium — souvent Compose — ou chemin overlay rapide). "
                        "Le commentaire IA n’a probablement pas été posté."
                    )
                    log_profile(idx, prompt_text, "priority_like_no_typed_comment")
                try:
                    take_screenshot(driver, idx)
                except Exception as e:
                    emsg = str(e)
                    print(f"[take_screenshot] error: {emsg}")
                    if is_uia2_crash_msg(emsg):
                        recover_session("screenshot after priority")
                        ensure_hinge_foreground()
                        wait_profile_ready()
                fails = 0
                hard_recovery_count = 0
                _last_profile_snapshot = None
                ensure_hinge_foreground()
                if not advanced:
                    swipe_to_next()
                    ensure_hinge_foreground()
                    wait_profile_ready()
            else:
                print("⚠️ Envoi KO — fallback like direct")

                # Vérifier si on est bloqué dans l'overlay
                if is_buggy_overlay_state() or is_comment_overlay_open():
                    print("🔧 Nettoyage overlay avant retry...")
                    clear_input_and_recover()
                    time.sleep(0.3)
                    like_locator = choose_like_locator_for_profile()

                if like_direct_no_overlay(like_locator):
                    print("✅ Like direct envoyé (fallback)")
                    log_profile(idx, prompt_text, "like_fallback")
                    try:
                        take_screenshot(driver, idx)
                    except Exception as e:
                        emsg = str(e)
                        print(f"[take_screenshot] error: {emsg}")
                        if is_uia2_crash_msg(emsg):
                            recover_session("screenshot after fallback")
                            ensure_hinge_foreground()
                            wait_profile_ready()
                    fails = 0
                    hard_recovery_count = 0
                    _last_profile_snapshot = None
                    swipe_to_next()
                    ensure_hinge_foreground()
                    wait_profile_ready()
                else:
                    print("❌ Like fallback KO — skip ce profil")
                    _safe_screenshot_on_failure(idx)
                    log_profile(idx, prompt_text, "fail_like_fallback")
                    fails += 1
                    swipe_to_next()
                    ensure_hinge_foreground()
                    wait_profile_ready()
        else:
            print("ℹ️ Pas de prompt — like direct")
            if like_direct_no_overlay(like_locator):
                print("✅ Like direct envoyé")
                log_profile(idx, "<no_prompt>", "like_direct")
                try:
                    take_screenshot(driver, idx)
                except Exception as e:
                    emsg = str(e)
                    print(f"[take_screenshot] error: {emsg}")
                    if is_uia2_crash_msg(emsg):
                        recover_session("screenshot after direct")
                        ensure_hinge_foreground()
                        wait_profile_ready()
                fails = 0
                hard_recovery_count = 0
                _last_profile_snapshot = None
            else:
                print("❌ Like direct impossible")
                _safe_screenshot_on_failure(idx)
                log_profile(idx, "<no_prompt>", "fail_like_direct")
                fails += 1
            swipe_to_next()
            ensure_hinge_foreground()
            wait_profile_ready()

        # --- Gestion des échecs : recovery au lieu de stop ---
        if fails >= MAX_FAILS:
            hard_recovery_count += 1
            print(f"⚠️ {fails} échecs consécutifs — hard recovery #{hard_recovery_count}")
            _safe_screenshot_on_failure(idx)

            if hard_recovery_count > MAX_HARD_RECOVERY:
                print("⛔ Trop de hard recoveries sans succès — arrêt définitif.")
                break

            _hard_recovery(f"fails={fails}")
            fails = 0
            _last_profile_snapshot = None
            # Ne pas incrementer idx pour retraiter depuis un état propre
            idx -= 1
            continue

        if detect_end_of_stack():
            print("⛔ Fin de pile détectée — stop.")
            break

        time.sleep(0.4)

except KeyboardInterrupt:
    print("🛑 Interrompu par l'utilisateur.")

finally:
    try:
        driver.quit()
    except Exception:
        pass
    print("✅ Terminé.")