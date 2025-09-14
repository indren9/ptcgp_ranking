# PTCGP Ranking — MARS (Meta‑Adjusted, Regularized Score)

Pipeline end‑to‑end per scraping, consolidamento e ranking dei mazzi con **MARS**: un punteggio composito che combina posteriore Beta–Binomiale, **MAS/SE/LB**, **Bradley–Terry** robusto e blending **meta vs encounter**. Il progetto è notebook‑first, modulare e riproducibile.

---

## 📦 Struttura del progetto

```
ptcgp_ranking/
├─ config/
│  ├─ alias_map.json
│  └─ config.yaml
├─ core/
│  ├─ consolidate.py        # alias, simmetrizzazione, score table filtrata
│  ├─ matrices.py           # build matrici W/L/T/WR e n_dir
│  ├─ nan_filter.py         # filtro NaN iterativo sugli assi
│  └─ normalize.py          # util di normalizzazione (alias/main)
├─ mars/
│  ├─ auto_k_cv.py          # AUTO_K-CV (log-lik predittiva OOF)
│  ├─ bt.py                 # Bradley–Terry robusto (filtro/pesi/MM ridge)
│  ├─ composite.py          # z-score mix e Score_%
│  ├─ config.py             # dataclass MARSConfig
│  ├─ core.py               # helpers comuni
│  ├─ coverage.py           # coverage & missing analysis
│  ├─ diagnostics.py        # packing diagnosis per logs
│  ├─ mas_lb.py             # MAS, SE, LB
│  ├─ meta.py               # blend meta/encounter con gap policy
│  ├─ pipeline.py           # orchestrazione run_mars(...)
│  ├─ posterior.py          # posteriori Beta–Binomiale (μ=0.5)
│  ├─ typing.py             # tipi / alias
│  └─ validate_io.py        # validatori IO
├─ scraper/
│  ├─ browser.py, session.py, decklist.py, matchups.py
├─ utils/
│  ├─ io.py                 # ROUTES + writer CSV/plot (latest + versioned)
│  └─ display.py            # show_ranking / show_wr_heatmap
├─ outputs/
│  ├─ Decklists/{raw, top_meta}
│  ├─ MatchupData/{raw, flat}
│  ├─ Matrices/{winrate, volumes, heatmap}
│  └─ RankingData/MARS      # SOLO `mars_ranking_*.csv`
├─ 1_scrape_core_preview.ipynb
├─ 2_core_mars_preview.ipynb
├─ README.md
└─ requirements.txt
```

---

## 🚀 Quick start

1) **Ambiente**
```bash
python -m venv .venv
# Windows
. .venv/Scripts/activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

2) **Notebook Parte 1 – Scrape & Core preview**
- Esegue scraping, alias+aggregazione, costruzione matrici, filtro NaN.
- Output contrattuali:
  - `outputs/Matrices/winrate/filtered_wr_latest.csv` (T×T, diag=NaN)
  - `outputs/Matrices/volumes/n_dir_latest.csv`     (T×T, diag=NaN)
  - `outputs/MatchupData/flat/score_latest.csv`     (post‑alias, **post‑filtro**, no mirror, entrambe direzioni)

3) **Notebook Parte 2 – MARS**
- Carica gli output della Parte 1 e calcola ranking + diagnostiche display.
- Salvataggi: **solo** `outputs/RankingData/MARS/mars_ranking_latest.csv` (+ copia versionata).  
  La heatmap WR viene salvata in `outputs/Matrices/heatmap/` come:
  - `wr_heatmap_latest.png`
  - `wr_heatmap_T{K}_{YYYYmmdd_HHMMSS}.png`

---

## ⚙️ Configurazione (`config/config.yaml`)

```yaml
logging:
  level: INFO

mars:
  # Posterior / LB / Composite
  MU: 0.5
  Z_PENALTY: 1.2
  ALPHA_COMPOSITE: 0.72

  # META blend
  AUTO_GAMMA: false
  GAMMA_META_BLEND: 0.30
  GAMMA_MIN: 0.10
  GAMMA_MAX: 0.60
  GAMMA_BASE: 0.10
  GAMMA_SLOPE: 1.5
  META_GAP_POLICY: encounter   # proportional | uniform | encounter

  # AUTO-K
  AUTO_K: true
  K_MIN: 0.10
  K_CONST_BOUNDS: [0.05, 50.0]
  INSTANT_APPLY_K: true
  REL_TOL_LL: 0.001
  BETA_EXPANSIONS: 2
  SEED: 42

  # BT
  N_MIN_BT_TARGET: 5
  BT_SOFT_POWER: null          # null => auto-continuo
  BT_NEAR_BAND: 0.10
  BT_USE_HARMONIC_N: true
  LAMBDA_RIDGE: 1.5
  MAX_BT_ITER: 500
  BT_TOL: 1e-6

  # Misc
  EPS: 1.0e-12
```

La dataclass **`mars.config.MARSConfig`** accetta le stesse chiavi (MAIUSCOLE).

---

## 🧠 MARS — versione “spiegata bene”

> Un ranking dei mazzi **stabile ma discriminante** che combina resa nel meta reale (oggi) e forza intrinseca dai matchup, anche con dati incompleti.

### 0) Cosa fa in una frase
Calcola per ogni mazzo un punteggio unico (`Score_%`) miscelando **MAS** (resa vs meta), **BT%** (forza “assoluta” dal grafo dei confronti) e prudenza via **smoothing Bayes** con **`LB = MAS − z · SE`**.

### 1) Input (già allineati)
- `filtered_wr` (T×T): winrate direzionali A→B in %, **T esclusi**; diagonale `NaN`.
- `n_dir` (T×T): volumi `N_dir = W + L`, coerenti con l’asse WR.
- Opzionali: `df_top_meta(_post_alias)`, `df_matchups_agg` (`Deck A,B,W,L,T,N`).

**Convenzioni**
- `WR(A→B) = W / (W + L)`, T esclusi.  
- Diagonale (mirror) non usata.  
- Asse già filtrato (stesso sottoinsieme per WR e N).

### 2) Notazione rapida
`p_hat(A→B)`, `p(B)`, `MAS(A)`, `SE(A)`, `LB(A)`, `BT%(A)`, `Score_%(A)` come nel glossario standard.

### 3) Step 1 — Smoothing Bayes per coppia (Beta–Binomiale, μ = 0.5)
Prior `Beta(mu*K, (1-mu)*K)` su `N_dir = W + L`. Posteriori in chiuso:
```
a = W + mu*K
b = L + (1 - mu)*K
p_hat = (W + mu*K) / (N_dir + K)
Var[p_hat] = (a * b) / ((a + b)^2 * (a + b + 1))
```
Forma di shrink:
```
p_hat = (K / (N + K)) * 0.5  +  (N / (N + K)) * (W / N)
```
Edge: `N_dir = 0` ⇒ esclusa da MAS; pareggi esclusi (opz. 0.5·T **prima** dello smoothing).

#### 3.b) AUTO_K‑CV (un solo K per tutte le coppie, 100% data‑driven)
- Celle off‑diag con `N_dir>0`; scala auto `beta_auto = sqrt(N_med * N_75)`.
- Split proporzionale (ρ=1/3) con minimi garantiti; griglia log‑spaced e clip `[0.05, 50]` + `K_min`.
- Scelta su LL predittiva OOF; tie → più piccolo. Espansione verso il basso se al bordo.
- Bootstrap leggero (50×) → mediana/IQR/mode; regola finale: **best**, **boot‑clipped** o **boundary‑override**.
- Log minimi: griglia/K*/K_used/motivo, `beta_auto`, `#expansions`, `ΔLL/100`, quantili di `r = K/(K+N)`, `r_small_median`.

### 4) Step 2 — Pesi di meta `p(B)` (share ⊗ encounter, **auto‑gamma** opzionale)
Gap‑recovery su top‑meta (encounter|proportional|uniform). Blend:
```
p(B) = (1 - gamma) * p_meta(B)  +  gamma * p_enc(B)
```
In MAS rinormalizza per riga sui soli avversari osservati:
```
w_A(B) = p(B) / sum_{C in Obs(A)} p(C)
```

### 5) Step 3 — MAS, SE, LB
```
MAS(A) = sum_B p(B) * p_hat(A→B)
SE(A)  = sqrt( sum_B p(B)^2 * Var[p_hat(A→B)] )
LB(A)  = MAS(A) - z * SE(A)
```

### 6) Step 4 — Bradley–Terry (filtro adattivo + soft‑weight, armonica ON)
Filtro su `s_bar ≥ s_min` con `s = N/(N+K_used)` e `s_min = N_min/(N_min+K_used)`.  
Pesi:
```
n_eff = n_base * (s_bar)^gamma     # gamma = BT_SOFT_POWER (auto-cont se None, ~[1.5, 2.1])
```
`n_base`: **armonica** se doppia direzione, altrimenti media.  
Stima MM con **ridge** (`LAMBDA_RIDGE`), normalizza `pi` (gmean=1), squash in `%` (sigmoide).  
Diagnostica: kept/dropped, near_thresh%, s_bar_median_kept, coverage min/med, HHI_lev(base), gamma, harmonic.

### 7) Step 5 — Composito finale (`Score_%`)
Standardizza `LB` e `BT` → `z(LB)`, `z(BT)`:
```
z_comp = alpha * z(LB) + (1 - alpha) * z(BT)
Score_% = 100 * Phi( z_comp / sqrt(2) )
```
Tie‑break: **LB%**, poi **BT%**, poi **N_eff/Coverage**.

### 8) Colonne in output
`Deck`, `Score_%`, `MAS_%`, `LB_%`, `BT_%`, `SE_%`, `N_eff`, `Opp_used`, `Opp_total`, `Coverage_%`.

### 9) Diagnostica utile
`corr(z(LB), z(BT))`, breakdown `MAS`, `TV` & `gamma` per meta blend, leverage edges/top‑5 e per deck, gate recap (`N_MIN_BT_TARGET`, `K_used`, `s_min`).

### 10) Default consigliati (rapidi)
```
MU = 0.5
Z_PENALTY = 1.2
ALPHA_COMPOSITE = 0.72
# Meta
AUTO_GAMMA opzionale; se off → GAMMA_META_BLEND = 0.30
GAMMA_BASE/SLOPE/MIN/MAX = 0.10 / 1.5 / 0.10 / 0.60
# Bradley–Terry
N_MIN_BT_TARGET = 5
BT_SOFT_POWER = None   # auto‑cont (~[1.5, 2.1]); 1.6 se fisso
BT_USE_HARMONIC_N = True
BT_NEAR_BAND = 0.10
LAMBDA_RIDGE = 1.5
```

### 11) Edge case
Buchi A→B rinormalizzati; righe vuote → MAS non informativo (resta BT).  
Assi non allineati → intersezione. Top‑meta assente → p_meta uniforme.

### 12) Perché funziona
Smoothing + MAS meta‑aware + BT regolarizzato (filtro/soft‑weight/armonica/ridge) + mix α → ranking **stabile**, **discriminante**, **meta‑sensibile**.

### 13) Pattern da leggere
`BT%` alto / `MAS%` basso = forte “assoluto”, meta sfavorevole.  
`LB% << MAS%` = incertezza (SE alta). Coverage bassa → prudenza.

### 14) Mini‑workflow di tuning
Step‑by‑step come da guida: osserva near_thresh, coverage, HHI, gamma auto; regola `LAMBDA_RIDGE` e `ALPHA_COMPOSITE` se necessario.

---

## 📄 Contratti dati (Parte 2 Input)

- `filtered_wr` (T×T): WR direzionali A→B in %, **diag=NaN**, asse = top‑meta post‑alias.
- `n_dir` (T×T): volumi W+L, **diag=NaN**, stesso asse.
- `score_latest.csv` (flat): `Deck A, Deck B, W, L, T, N, WR_dir` (+ `Winrate` alias di `WR_dir`).

*(Opz.)* `top_meta_decklist_latest.csv` per il blend meta; `p_enc` = somma colonna di `n_dir` rinormalizzata.

---

## 🧭 ROUTES & salvataggi

`utils/io.py` centralizza le destinazioni.
- `filtered_wr` → `outputs/Matrices/winrate/`
- `n_dir`      → `outputs/Matrices/volumes/`
- `matchup_score_table` → `outputs/MatchupData/flat/`
- `mars_ranking` → **solo** `outputs/RankingData/MARS/`
- **Heatmap** WR → `outputs/Matrices/heatmap/` (`wr_heatmap_latest.png`, `wr_heatmap_T{K}_{YYYYmmdd_HHMMSS}.png`)

---

## 👀 Utilities per notebook

- `utils.display.show_ranking(...)` — anteprima Top‑N.  
- `utils.display.show_wr_heatmap(...)` — heatmap WR (opz. salvataggio).

---

## 🔁 Riproducibilità

Seed (`SEED`) in AUTO_K; pipeline deterministica; logging pulito in INFO.

---

## 🩺 Troubleshooting

File Parte 1 mancanti / shape mismatch / `corr` NaN: vedi note nella sezione precedente.

---

## 🛣️ Roadmap (breve)

- Export opzionale diagnosi come JSON.  
- Mini‑benchmark locale.  
- Parametrizzare notebook → CLI opzionale.

---

## 📜 Licenza

**MIT** — vedi file [`LICENSE`](LICENSE).  
Copyright (c) 2025 Andrea Visentin.

Se riusi il codice, mantieni **copyright** e **testo della licenza** nei file distribuiti.

**Attribuzione consigliata / citazione**  
Se citi il progetto in tesi, paper o post, puoi usare questo formato minimale:
> Visentin, A. (2025). *PTCGP Ranking — MARS*. MIT License. https://github.com/indren9/ptcgp_ranking

Sono inclusi anche:
- `CITATION.cff` per strumenti come GitHub/Zenodo;
- `NOTICE` con disclaimer su marchi e fonti dati.

## 👤 Autore & Contatti

Andrea Visentin · GitHub: https://github.com/indren9
Issue tracker: usa le *Issues* del repo una volta pubblicato: https://github.com/indren9/ptcgp_ranking
