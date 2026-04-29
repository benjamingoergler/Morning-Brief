# Morning Brief

Site auto-généré pour la préparation matinale d'un trader NQ futures. Trois onglets :

1. **Préparation du jour** — résumés des 3 articles Financial Juice (Morning Juice EU, US Wrap de la veille, Morning Juice US) + table des dockets économiques avec heures Paris
2. **VVA Zones** — le dashboard VVA stat, intégré tel quel
3. **Historique** — toutes les journées passées, cliquer sur une date recharge la prep avec ce jour-là

Le site est hébergé sur GitHub Pages (URL fixe à bookmarker), reconstruit automatiquement deux fois par jour par GitHub Actions (08:30 CEST puis 15:00 CEST, du lundi au vendredi). Aucune action manuelle requise.

## Coût

**100 % gratuit.** Aucun abonnement, aucune carte de crédit demandée nulle part.

- GitHub : free tier (repo public + Pages + Actions)
- Google Gemini API : free tier (10 requêtes/min, 250/jour ; on en consomme 2/jour)
- Anthropic n'est pas utilisé

## Setup (une seule fois, ~15 minutes)

### 1. Créer un compte GitHub

Si déjà fait, passer à 2.

Aller sur [github.com](https://github.com) → "Sign up" → suivre les étapes. Un email + mot de passe suffit. Plan gratuit OK.

### 2. Créer un nouveau repo et y uploader ces fichiers

Deux façons :

**A. Web (le plus simple si tu n'utilises pas Git)**

1. Sur [github.com](https://github.com), bouton "New repository" en haut à droite
2. Nom : `morning-brief` (ou ce que tu veux)
3. Visibilité : **Public** (sinon GitHub Pages gratuit ne marche pas)
4. NE PAS cocher "Add a README file" (on en a déjà un)
5. "Create repository"
6. Sur la page du repo qui s'affiche, "uploading an existing file"
7. Drag & drop **le contenu** du dossier décompressé (les fichiers et dossiers, pas le dossier `morning-brief` lui-même)
8. En bas, "Commit changes"

**B. Git en ligne de commande**

```bash
cd morning-brief
git init
git remote add origin https://github.com/TONPSEUDO/morning-brief.git
git add .
git commit -m "Initial commit"
git branch -M main
git push -u origin main
```

### 3. Récupérer une clé API Gemini (gratuite, 2 minutes)

1. Aller sur [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Se connecter avec un compte Google (n'importe lequel)
3. Bouton **"Create API key"** (en haut à droite)
4. Sélectionner ou créer un projet (n'importe quel nom, ex. "morning-brief")
5. **Copier la clé** qui s'affiche (commence par `AIza...`)

> Pas de carte de crédit demandée. Pas de pré-paiement. Free tier permanent. La seule contrepartie : Google peut utiliser tes prompts/réponses pour améliorer leurs modèles. Pour des résumés d'articles publics FJ, ça nous va.

### 4. Ajouter la clé Gemini comme "secret" dans le repo GitHub

Sur la page GitHub du repo créé à l'étape 2 :

1. Onglet **Settings** (en haut)
2. Menu de gauche : **Secrets and variables** → **Actions**
3. Bouton vert **New repository secret**
4. Name : `GEMINI_API_KEY` (exactement comme ça, majuscules/underscore)
5. Secret : coller la clé `AIza...`
6. **Add secret**

### 5. Activer GitHub Pages

Toujours dans Settings :

1. Menu de gauche : **Pages**
2. Source : **Deploy from a branch**
3. Branch : `main`, Folder : `/docs`
4. **Save**

GitHub affiche en haut "Your site is live at `https://TONPSEUDO.github.io/morning-brief/`" (ça met 1-2 min la première fois).

### 6. Lancer le premier build manuellement

Le cron tourne automatiquement aux heures fixées, mais pour tester tout de suite :

1. Onglet **Actions** du repo
2. Si une bannière demande "Enable workflows", cliquer dessus
3. Sidebar gauche : **Build morning brief**
4. Bouton **Run workflow** → **Run workflow** (vert)
5. Attendre ~1 minute, le workflow tourne et fait un commit auto
6. Recharger l'URL `https://TONPSEUDO.github.io/morning-brief/` → ton brief est là !

### 7. Bookmarker l'URL

Sur ton tel et ton ordi. C'est tout. Plus jamais aucune manip.

## Comportement automatique

| Heure UTC | Heure Paris (été) | Action |
|-----------|---|---|
| 06:30 | 08:30 CEST | Build matinal : Morning Juice EU + US Wrap d'hier |
| 13:00 | 15:00 CEST | Build mi-journée : ajoute Morning Juice US dès qu'il est publié |

Le script est **idempotent** : s'il tourne en doublon, ou si Morning Juice US n'est pas encore publié à 8h30, le brief de 15h vient compléter le fichier existant sans rien écraser.

Pas de build le weekend (les marchés sont fermés, FJ ne publie pas).

## Structure du repo

```
morning-brief/
├── build.py                # script principal
├── requirements.txt
├── README.md
├── .github/workflows/
│   └── build.yml           # cron + build + commit
└── docs/                   # ce que GitHub Pages sert
    ├── index.html          # template statique avec JS
    ├── vva.html            # ton VVA dashboard
    ├── manifest.json       # liste des jours (généré)
    └── archive/
        └── 2026-04-24.json # données du jour (généré)
```

Le template `docs/index.html` est statique. Il fetch `manifest.json` au chargement, puis charge `archive/<today>.json`. Les jours d'historique sont chargés à la demande quand tu cliques.

## Customisation

**Changer les heures du cron** : éditer `.github/workflows/build.yml`, lignes `cron:`. Format standard cron, en UTC.

**Changer les pays/thèmes des résumés** : éditer le `SUMMARY_PROMPT` dans `build.py`.

**Modifier le design du site** : éditer `docs/index.html` (CSS dans le `<style>`, layout dans le HTML, comportement dans le `<script>`). Push sur main → GitHub Pages se rafraîchit en 1-2 min.

**Mettre à jour le VVA** : remplacer `docs/vva.html` par la nouvelle version, push sur main.

**Changer de modèle Gemini** : dans `build.py`, modifier `GEMINI_MODEL`. Options : `gemini-2.5-flash` (par défaut, équilibre qualité/throughput), `gemini-2.5-flash-lite` (plus de quota mais qualité moindre), `gemini-2.5-pro` (meilleure qualité mais quota réduit à 100/jour).

## Troubleshooting

**Le workflow Action a échoué (croix rouge)**
→ Cliquer sur le run dans l'onglet Actions, regarder les logs. Causes typiques :
- Clé Gemini absente ou invalide → vérifier le secret `GEMINI_API_KEY`
- FJ a changé sa structure HTML → ouvrir une issue dans ce repo (ou me ping)

**Le site affiche "Erreur : manifest.json missing"**
→ Le premier build n'a pas tourné. Va dans Actions → Run workflow manuellement.

**Le brief affiche un article comme "(pas encore publié)"**
→ Normal le matin avant 13h CEST pour le Morning Juice US. Le build de 15h le récupèrera.

**TTS ne marche pas**
→ Sur Windows 11 : Paramètres → Heure et langue → Langue → ajouter le français + télécharger le pack vocal.
→ Sur iOS : voix Siri française installée par défaut, pas d'action.
→ Sur Android : selon le constructeur, voix Google TTS française à activer dans les paramètres.

## Licence

À toi. C'est ton outil personnel.
