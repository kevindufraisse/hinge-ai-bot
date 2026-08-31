# Hinge AI Bot

Bot Python qui pilote l’app Android **Hinge** via [Appium](https://appium.io/), lit les prompts d’un profil, génère un commentaire avec **OpenAI**, puis envoie un like (avec ou sans commentaire).

> **Avertissement.** Automatiser Hinge va à l’encontre des conditions d’utilisation de l’app. Risques : ban de compte, blocage. Usage à tes risques. Projet à visée technique / éducative.

Fork amélioré du projet original [nagesh-cheviti/hinge-ai-bot](https://github.com/nagesh-cheviti/hinge-ai-bot) : OpenAI à la place de Gemini, récupération de session UiAutomator2, meilleure lecture des profils Compose, collage du commentaire via presse-papiers.

---

## Comment ça marche

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Émulateur      │     │  Appium Server   │     │  Python         │
│  Android        │◄───►│  (port 4723)     │◄───►│  appium_bot.py  │
│  + app Hinge    │     │  UiAutomator2    │     │                 │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                                                          ▼
                                                 ┌─────────────────┐
                                                 │ ai_commenter.py │
                                                 │ OpenAI (GPT)    │
                                                 └─────────────────┘
```

1. Tu lances un émulateur Android avec Hinge déjà ouvert et connecté.
2. Tu lances le serveur Appium (`appium`).
3. Tu lances `python3 appium_bot.py`.
4. Le script se connecte à Appium, garde Hinge au premier plan, et enchaîne les profils en boucle.

Sur **chaque profil** :

1. Il attend que la carte soit prête (bouton Like, bulle de prompt, photo…).
2. Il tente de lire un **prompt Hinge** + la **réponse** de la personne (`find_prompt_text`).
3. **S’il y a un prompt valide** → OpenAI génère un commentaire court → le bot ouvre le like, colle le texte, envoie *Send priority like*.
4. **Sinon** (pas de prompt, champ Compose illisible, commentaire trop long) → like simple via *Send like*.
5. Il log l’action dans `logs/run.log` et prend un screenshot dans `shots/`.
6. Il swipe vers le profil suivant.

Le bot s’arrête tout seul si :

- plus de profils (« come back later », « that’s all for now », …) ;
- Hinge bloque les likes (« You’re over the limit » — trop de conversations sans réponse) ;
- trop d’échecs d’affilée après plusieurs *hard recoveries* ;
- tu interromps avec Ctrl+C.

---

## Fichiers

| Fichier | Rôle |
|---|---|
| `appium_bot.py` | Cœur du bot : session Appium, lecture d’écran, likes, commentaires, swipe, récupération d’erreurs. |
| `ai_commenter.py` | Appelle OpenAI, génère plusieurs candidats, en choisit un (score : question, longueur, peu de formules génériques). |
| `config.py` | Capacités Appium + lecture de la clé API depuis `.env`. |
| `utils.py` | Log texte (`logs/run.log`) et screenshots (`shots/`). |
| `.env` | Ta clé OpenAI — **jamais commitée**. Copie depuis `.env.example`. |

### Détail `appium_bot.py`

- **Session** : connexion à `http://127.0.0.1:4723` avec retries, recréation si UiAutomator2 crash.
- **Lecture des prompts** : d’abord les IDs Android classiques (`prompt_bubble` / `question` / `answer`), puis le XML `page_source` (Hinge en Jetpack Compose n’expose souvent plus les `TextView`).
- **Commentaire** : le champ n’est pas toujours un `EditText`. Le bot tape aux coordonnées au-dessus du bouton *Send priority like*, puis colle via le presse-papiers (évite `adb input text` qui casse les apostrophes / l’UTF-8).
- **Robustesse** : overlay coincé, même profil en boucle, Hinge qui passe en arrière-plan, file de likes saturée.

### Détail `ai_commenter.py`

- Modèle : `gpt-5.2` (modifiable dans le fichier).
- Max ~80 caractères, une ligne, sans guillemets.
- 4 candidats générés, le meilleur est retenu.
- En cas d’erreur API : fallback `"do you want to know a secret?"`.

---

## Prérequis

- Python 3.10+
- [Android Studio](https://developer.android.com/studio) + un AVD (ex. Pixel 6, API 35/36)
- [Appium](https://appium.io/docs/en/latest/quickstart/) + driver **UiAutomator2**
- Compte Hinge déjà connecté **dans l’émulateur**
- Clé API [OpenAI](https://platform.openai.com/api-keys)

Installer Appium (exemple) :

```bash
npm install -g appium
appium driver install uiautomator2
```

---

## Installation

```bash
git clone https://github.com/kevindufraisse/hinge-ai-bot.git
cd hinge-ai-bot

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
# Édite .env et mets ta clé OPENAI_API_KEY
```

Vérifie dans `config.py` / `.env` que `APPIUM_DEVICE_NAME` et `APPIUM_PLATFORM_VERSION` correspondent à ton émulateur (`adb devices` + `adb shell getprop ro.build.version.release`).

---

## Lancer

Terminal 1 — émulateur :

```bash
emulator -avd Pixel_6
```

Terminal 2 — Appium :

```bash
appium
```

Terminal 3 — ouvre Hinge à la main dans l’émulateur (Discover), puis :

```bash
source .venv/bin/activate
python3 appium_bot.py
```

Variables d’environnement utiles :

| Variable | Défaut | Rôle |
|---|---|---|
| `OPENAI_API_KEY` | — | Clé OpenAI (obligatoire) |
| `APPIUM_SERVER_URL` | `http://127.0.0.1:4723` | URL du serveur Appium |
| `APPIUM_DEVICE_NAME` | `emulator-5554` | Device vu par `adb devices` |
| `APPIUM_PLATFORM_VERSION` | `16` | Version Android |

---

## Inspector (debug UI)

Si tu veux inspecter l’arbre d’accessibilité Hinge (Appium Inspector) :

- Host : `127.0.0.1`
- Port : `4723`
- Path : `/`

```json
{
  "platformName": "Android",
  "appium:deviceName": "emulator-5554",
  "appium:appPackage": "co.hinge.app",
  "appium:appActivity": "co.hinge.app.ui.AppActivity",
  "appium:automationName": "UiAutomator2",
  "appium:noReset": true,
  "appium:newCommandTimeout": 600
}
```

---

## Limites connues

- Hinge change souvent son UI (Compose). Les locateurs cassent ; le bot a des fallbacks XML / coordonnées, mais ce n’est pas infaillible.
- Un commentaire n’est envoyé que si le champ est trouvable et que *Send priority like* se ferme après le tap.
- Quota Hinge : likes, Rose, file de conversations sans réponse.
- Les screenshots dans `shots/` et les logs dans `logs/` restent **en local** (gitignore). Ne les committe pas : ce sont des profils de vraies personnes.
