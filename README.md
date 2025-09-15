# PTCGP Ranking — MARS (Meta-Adjusted, Regularized Score)

Pipeline end-to-end per scraping, consolidamento e ranking dei mazzi con **MARS**: un punteggio composito che combina posteriore Beta–Binomiale, **MAS/SE/LB**, **Bradley–Terry** robusto e blending **meta vs encounter**. Il progetto è notebook-first, modulare e riproducibile.

---

## Novità — 2025-09-15

- **Legenda come banner PNG**: generata in `mars/report.py` e **incollata** in `00_Legenda` (niente tabella di testo). Layout **verticale** e leggibile:
  1) *Che cos’è* → 2) **01_Summary** (ranking) → 3) **Fogli per deck** (A→tutti) → 4) **Palette colori**.  
  Il banner si adatta in larghezza (simile all’esempio), con wrapping automatico.
- **Riordino fogli robusto**: `_reorder_excel_sheets_robust(...)` ordina i fogli secondo il ranking anche con nomi **sanificati/troncati/duplicati**.
- **Semafori `gap_pp` affidabili**: lo styling dei fogli per-deck usa **CellIsRule** (>=8 / <=−8 **rosso**, e 4..8 / −8..−4 **giallo**) e prova a convertire testi numerici in numeri prima di applicare le regole.
- **Colonne opzionali nei fogli per-deck**: puoi **escludere** `SE_dir_%`, `w_A(B)_%` e `MAS_contrib_pp` senza toccare i calcoli (vedi parametri del writer).
- **Riga “Mirror”**: evidenziata in **grigio** su tutta la riga; `Opponent` in corsivo.
- **Scrittura Excel atomica + retry** in `utils/io.write_excel_versioned_styled(...)`, con fallback su file `*_LOCKED_*.xlsx` se il target è bloccato (OneDrive/Excel aperto).
- **Colonne leggenda** auto-larghezza & wrapping (Campo/Descrizione/Colore) e **swatch** colore in `00_Legenda`.

> Nota: il precedente riordino via `utils.io.reorder_excel_sheets` non è più necessario — è interno al writer del report.

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
│  ├─ pipeline.py           # orchestratore run_mars(...)
│  ├─ posterior.py          # posteriori Beta–Binomiale (μ=0.5)
│  ├─ report.py             # writer Excel + legenda-banner + riordino fogli
│  └─ validate_io.py        # validatori IO
├─ scraper/
│  ├─ browser.py, session.py, decklist.py, matchups.py
├─ utils/
│  ├─ io.py                 # ROUTES + writer CSV/plot/excel (styled & atomic)
│  └─ display.py            # show_ranking / show_wr_heatmap
├─ outputs/
│  ├─ Decklists/{raw, top_meta}
│  ├─ MatchupData/{raw, flat}
│  ├─ Matrices/{winrate, volumes, heatmap}
│  └─ RankingData/MARS      # mars_ranking_*.csv + Report/
├─ 1_scrape_core_preview.ipynb
├─ 2_core_mars_preview.ipynb
├─ 3_run_all.ipynb
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
  - `outputs/MatchupData/flat/score_latest.csv`     (post-alias, **post-filtro**, no mirror, entrambe direzioni)

3) **Notebook Parte 2 – MARS**
- Carica gli output della Parte 1 e calcola ranking + diagnostiche display.
- Salvataggi: **solo** `outputs/RankingData/MARS/mars_ranking_latest.csv` (+ copia versionata).  
  La heatmap WR viene salvata in `outputs/Matrices/heatmap/` come:
  - `wr_heatmap_latest.png`
  - `wr_heatmap_T{K}_{YYYYmmdd_HHMMSS}.png`

**Parte 3 — Run All (opzionale ma consigliato)**
1. Apri `3_run_all.ipynb`.
2. Seleziona il kernel della tua venv (Python: Select Interpreter).
3. Esegui **Run All**.
4. Verifica gli output in `outputs/RankingData/MARS/Report/` (file *versioned* e *latest*).

---
### Requisiti aggiuntivi

Aggiungi a `requirements.txt` (se non presenti):
```
openpyxl>=3.1,<3.2
xlsxwriter>=3.2,<3.3     # consigliato per la scrittura
pillow>=10,<11            # per il banner PNG della legenda
matplotlib>=3.8,<3.9      # per il font manager usato nel banner
```
> Il writer prova automaticamente `xlsxwriter` e poi `openpyxl`. Lo styling (semafori / top-K / swatch) richiede `openpyxl` lato post-scrittura.

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

#### 3.b) AUTO_K-CV (un solo K per tutte le coppie, 100% data-driven)
- Celle off-diag con `N_dir>0`; scala auto `beta_auto = sqrt(N_med * N_75)`.
- Split proporzionale (ρ=1/3) con minimi garantiti; griglia log-spaced e clip `[0.05, 50]` + `K_min`.
- Scelta su LL predittiva OOF; tie → più piccolo. Espansione verso il basso se al bordo.
- Bootstrap leggero (50×) → mediana/IQR/mode; regola finale: **best**, **boot-clipped** o **boundary-override**.
- Log minimi: griglia/K*/K_used/motivo, `beta_auto`, `#expansions`, `ΔLL/100`, quantili di `r = K/(K+N)`, `r_small_median`.

### 4) Step 2 — Pesi di meta `p(B)` (share ⊗ encounter, **auto-gamma** opzionale)
Blend:
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

### 6) Step 4 — Bradley–Terry (filtro adattivo + soft-weight, armonica ON)
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
Tie-break: **LB%**, poi **BT%**, poi **N_eff/Coverage**.

### 8) Colonne in output
`Deck`, `Score_%`, `MAS_%`, `LB_%`, `BT_%`, `SE_%`, `N_eff`, `Opp_used`, `Opp_total`, `Coverage_%`.

### 9) Diagnostica utile
`corr(z(LB), z(BT))`, breakdown `MAS`, `TV` & `gamma` per meta blend, leverage edges/top-5 e per deck, gate recap (`N_MIN_BT_TARGET`, `K_used`, `s_min`).

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
BT_SOFT_POWER = None   # auto-cont (~[1.5, 2.1]); 1.6 se fisso
BT_USE_HARMONIC_N = True
BT_NEAR_BAND = 0.10
LAMBDA_RIDGE = 1.5
```

### 11) Edge case
Buchi A→B rinormalizzati; righe vuote → MAS non informativo (resta BT).  
Assi non allineati → intersezione. Top-meta assente → p_meta uniforme.

### 12) Perché funziona
Smoothing + MAS meta-aware + BT regolarizzato (filtro/soft-weight/armonica/ridge) + mix α → ranking **stabile**, **discriminante**, **meta-sensibile**.

### 13) Pattern da leggere
`BT%` alto / `MAS%` basso = forte “assoluto”, meta sfavorevole.  
`LB% << MAS%` = incertezza (SE alta). Coverage bassa → prudenza.

### 14) Mini-workflow di tuning
Osserva near_thresh, coverage, HHI, gamma auto; regola `LAMBDA_RIDGE` e `ALPHA_COMPOSITE` se necessario.

---

## Report Excel per-deck (ordinato per ranking)

Genera un workbook con `00_Legenda`, `01_Summary` e un foglio per ogni deck in **ordine di ranking**.

```python
from pathlib import Path
from mars.report import write_pairs_by_deck_report

versioned_path, latest_path, meta = write_pairs_by_deck_report(
    ranking_df=df_rank_mars,          # >= Deck, Score_%
    filtered_wr=filtered_wr,          # T×T %, diag NaN
    n_dir=n_matrix_filtered,          # T×T N=W+L, diag NaN
    p_blend=meta_weights_series,      # pesi MAS sull’asse
    K_used=K_used,
    score_flat=score_latest_flat,     # opzionale (abilita W/L/N e WR_real_% da flat)
    mu=0.5,
    gamma=gamma,                      # opzionale
    include_posterior_se=False,       # per nascondere SE_dir_% nei fogli per-deck
    include_binom_se=True,
    include_counts=True,
    include_self_row=True,
    # per NON creare colonne di peso/contributo nei fogli per-deck:
    include_weight_col=False,
    include_mas_contrib_col=False,
    out_dir=Path("outputs/RankingData/MARS/Report"),
)
print("Report scritto:", versioned_path, latest_path)
```

### Styling applicato automaticamente
- **gap_pp**: rosso se `|gap_pp| ≥ 8`, giallo se `4 ≤ |gap_pp| < 8` (valido anche se Excel ha celle in formato testo con numeri).
- **Top-K** su `MAS_contrib_pp` (se presente) via regola nativa `top10` (rank=K).
- **Mirror**: intera riga grigia; `Opponent` in corsivo.
- **00_Legenda**: colonne con larghezze ottimizzate, wrapping su Campo/Descrizione e **swatch** nella colonna `Colore`.

---

## 📄 Contratti dati (Parte 2 Input)

- `filtered_wr` (T×T): WR direzionali A→B in %, **diag=NaN**, asse = top-meta post-alias.
- `n_dir` (T×T): volumi W+L, **diag=NaN**, stesso asse.
- `score_latest.csv` (flat): `Deck A, Deck B, W, L, T, N, WR_dir` (+ `Winrate` alias di `WR_dir`).

*(Opz.)* `top_meta_decklist_latest.csv` per il blend meta; `p_enc` = somma colonna di `n_dir` rinormalizzata.

---

## 📓 Notebook 3 — `3_run_all.ipynb`

Notebook **end-to-end** per eseguire l’intera pipeline in un colpo solo.

**Pipeline eseguita (ordine):**
1. **Scrape & caching** (Notebook 1): decklist/top-meta e matchups; salvataggi in `outputs/Decklists/` e `outputs/MatchupData/`.
2. **Core prep**: aliasing, consolidamento score table filtrata, simmetrizzazione direzionale, costruzione matrici `filtered_wr` e `n_dir`, filtro NaN.
3. **MARS** (Notebook 2): `AUTO_K-CV`, MAS/LB/BT, `Score_%`.
4. **Report Excel**: `write_pairs_by_deck_report(...)` con **riordino automatico** dei fogli secondo il ranking (top→bottom) e **banner legenda**.

**Prerequisiti:**
- Ambiente attivo (venv) e dipendenze installate (`pip install -r requirements.txt`).
- `openpyxl` presente (per Excel). 
- File di config e alias pronti (`config/config.yaml`, `config/alias_map.json`).

**Output principali:**
- Ranking: `outputs/RankingData/MARS/mars_ranking_latest.csv` (+ versionati).
- Report: `outputs/RankingData/MARS/Report/pairs_by_deck_T{T}_MARS_{timestamp}.xlsx` e `pairs_by_deck_T{T}_latest.xlsx`.

**Troubleshooting:**
- *ImportError*: verifica kernel/venv e dipendenze.
- *Excel non ordinato / colori assenti*: usa il `report.py` e `utils/io.py` aggiornati; verifica `openpyxl` installato.
- *Percorsi/permessi*: controlla i path in `utils/io.py`; le cartelle vengono create se mancano.

---

## 🧭 ROUTES & salvataggi

`utils/io.py` centralizza le destinazioni.
- `filtered_wr` → `outputs/Matrices/winrate/`
- `n_dir`      → `outputs/Matrices/volumes/`
- `matchup_score_table` → `outputs/MatchupData/flat/`
- `mars_ranking` → **solo** `outputs/RankingData/MARS/`
- **Heatmap** WR → `outputs/Matrices/heatmap/` (`wr_heatmap_latest.png`, `wr_heatmap_T{K}_{YYYYmmdd_HHMMSS}.png`)

---

## 🔁 Riproducibilità

Seed (`SEED`) in AUTO_K; pipeline deterministica; logging pulito in INFO.

---

## 📜 Licenza

**MIT** — vedi file [`LICENSE`](LICENSE).  
Copyright (c) 2025 Andrea Visentin.

**Attribuzione consigliata / citazione**  
> Visentin, A. (2025). *PTCGP Ranking — MARS*. MIT License. https://github.com/indren9/ptcgp_ranking

---

## 👤 Autore & Contatti

Andrea Visentin · GitHub: https://github.com/indren9
