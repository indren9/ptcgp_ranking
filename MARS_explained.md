# MARS — Meta-Adjusted, Regularized Score (versione “spiegata bene”)

> Un ranking dei mazzi **stabile ma discriminante** che combina resa nel meta reale (oggi) e forza intrinseca dai matchup, anche con dati incompleti.

---

## Indice

- [0) Cosa fa in una frase](#0-cosa-fa-in-una-frase)
- [1) Input (già allineati)](#1-input-già-allineati)
- [2) Notazione rapida](#2-notazione-rapida)
- [3) Step 1 — Smoothing Bayes (Beta–Binomiale)](#3-step-1--smoothing-bayes-beta–binomiale)
  - [3.b) AUTO\_K-CV (K unico, data-driven)](#3b-auto_k-cv-k-unico-data-driven)
- [4) Step 2 — Pesi di meta p(B) (auto-γ opzionale)](#4-step-2--pesi-di-meta-pb-auto-γ-opzionale)
- [5) Step 3 — MAS, SE, LB](#5-step-3--mas-se-lb)
- [6) Step 4 — Bradley–Terry (filtro adattivo + soft-weight, armonica ON)](#6-step-4--bradley–terry-filtro-adattivo--soft-weight-armonica-on)
- [7) Step 5 — Composito finale (Score\_%)](#7-step-5--composito-finale-score_)
- [8) Colonne in output](#8-colonne-in-output)
- [9) Diagnostica utile](#9-diagnostica-utile)
- [10) Default consigliati (rapidi)](#10-default-consigliati-rapidi)
- [11) Edge case](#11-edge-case)
- [12) Perché funziona](#12-perché-funziona)
- [13) Come leggere i pattern](#13-come-leggere-i-pattern)
- [14) Mini-workflow di tuning](#14-mini-workflow-di-tuning)
- [Nota finale su AUTO\_K-CV](#nota-finale-su-auto_k-cv)

---

## 0) Cosa fa in una frase

Calcola per ogni mazzo un punteggio unico `Score_%` miscelando **MAS**, **BT%** e la prudenza **LB = MAS − z · SE**.

---

## 1) Input (già allineati)

- `filtered_wr` (T×T): winrate direzionali A→B in %, **T esclusi**; diagonale `NaN`.
- `n_matrix_filtered` (T×T): volumi `N_dir = W + L` coerenti con l’asse WR.
- Opzionali: `df_top_meta(_post_alias)`, `df_matchups_agg` (`Deck A,B,W,L,T,N`).

**Convenzioni**: `WR(A→B) = W / (W + L)` (T esclusi). La diagonale (mirror) non si usa. L’asse è già filtrato (stesso sottoinsieme per WR e N).

---

## 2) Notazione rapida

- `\hat p(A\to B)`: posteriore della probabilità che A batta B.  
- `p(B)`: peso di meta dell’avversario (somma 1).  
- `MAS(A)`: resa attesa di A vs meta.  
- `SE(A)`: incertezza su `MAS`.  
- `LB(A) = MAS - z\cdot SE`.  
- `BT%(A)`: forza Bradley–Terry.  
- `Score_%(A)`: mix normalizzato di `LB%` e `BT%`.

---

## 3) Step 1 — Smoothing Bayes (Beta–Binomiale)

**Modello** (con `\mu = 0.5` fisso; prior `\mathrm{Beta}(\mu K, (1-\mu)K)`, osservazioni su `N_{\mathrm{dir}}=W+L`).

$$
a = W + \mu K,\qquad b = L + (1-\mu)K.
$$

Posteriori in chiuso:
$$
\hat p \;=\; \frac{W + \mu K}{N_{\mathrm{dir}} + K},
\qquad
\operatorname{Var}[\hat p] \;=\; \frac{ab}{(a+b)^2 (a+b+1)}.
$$

**Shrink esplicito**
$$
\hat p \;=\; \frac{K}{N+K}\cdot 0.5 \;+\; \frac{N}{N+K}\cdot \frac{W}{N}.
$$

**Edge case**  
- `N_dir = 0`: la coppia non entra in `MAS`; BT userà altre connessioni.  
- Direzioni non complementari: BT riconcilia a livello di coppia.  
- Pareggi: di default esclusi; alternativa con `0.5·T` **prima** dello smoothing (opzione supportata).

### 3.b) AUTO_K-CV (K unico, data-driven)

**Idea**: scegli `K` massimizzando la **log-likelihood predittiva out-of-fold**, con scala auto-adattiva e robusta.

- **Scala auto**: 
$$
\beta_{\mathrm{auto}} \;=\; \sqrt{N_{\mathrm{med}} \cdot N_{75}}.
$$

- **Griglia** (log): 
$$
K \in \Big\{\tfrac{\beta}{4},\ \tfrac{\beta}{2},\ \beta,\ 2\beta,\ 4\beta\Big\},\quad \text{clip in } [0.05, 50].
$$

- **Selezione**: 
$$
K^* \;=\; \arg\max_K \ \mathcal{L}(K), \quad
\text{tie }(\le 0.1\%) \Rightarrow \text{scegli il più piccolo}.
$$

- **Confidenza coerente con lo shrink**:
$$
s(A\to B) \;=\; \frac{N}{N + K_{\mathrm{used}}} \in [0,1].
$$

> Log minimi: griglia `K`, `K*`, `K_used` + motivo, `\beta_{\mathrm{auto}}`, `ΔLL/100`, quantili di `r = K_{\mathrm{used}}/(K_{\mathrm{used}}+N)` e `r_small_median` su celle con `N` piccoli.

---

## 4) Step 2 — Pesi di meta p(B) (auto-γ opzionale)

Blend fra meta dichiarato e incontri reali.

- **Blend**:
$$
p(B) \;=\; (1-\gamma)\,p_{\mathrm{meta}}(B) \;+\; \gamma\,p_{\mathrm{enc}}(B).
$$

- **Rinormalizzazione per riga** (solo avversari osservati):
$$
w_A(B) \;=\; \frac{p(B)}{\sum_{C\in \mathrm{Obs}(A)} p(C)}.
$$

- **AUTO\_GAMMA** (opzionale):
$$
\gamma \;=\; \mathrm{clip}\!\big(\mathrm{base} + \mathrm{slope}\cdot d_{\mathrm{TV}},\ \mathrm{min},\ \mathrm{max}\big).
$$

---

## 5) Step 3 — MAS, SE, LB

$$
\mathrm{MAS}(A) \;=\; \sum_B p(B)\,\hat p(A\to B), \qquad
\mathrm{SE}(A)  \;=\; \sqrt{\sum_B p(B)^2\,\operatorname{Var}[\hat p(A\to B)]}, \qquad
\mathrm{LB}(A)  \;=\; \mathrm{MAS}(A) - z\,\mathrm{SE}(A).
$$

---

## 6) Step 4 — Bradley–Terry (filtro adattivo + soft-weight, armonica ON)

**Confidenza e soglia adattiva**
$$
s(i\to j)=\frac{N(i\to j)}{N(i\to j)+K_{\mathrm{used}}}, \qquad
\bar s(ij)=\text{media dei } s(i\to j).
$$

Soglia:
$$
s_{\min}=\frac{N_{\min}}{N_{\min}+K_{\mathrm{used}}}.
$$

> Default: \(N_{\min}=5 \Rightarrow s_{\min}\approx 0.39\) quando \(K_{\mathrm{used}}\in[6,10]\).  
> Includiamo la coppia \((i,j)\) solo se \(\bar s(ij)\ge s_{\min}\).

**Pesi del confronto**
- **Base** \(n_{\mathrm{base}}\): media **armonica** dei volumi direzionali se **entrambi > 0** (altrimenti media aritmetica).  
- **Soft-weight** (penalizza near-threshold, neutro sui robusti):
$$
n_{\mathrm{eff}} \;=\; n_{\mathrm{base}}\cdot \big(\bar s(ij)\big)^{\gamma}, \qquad \gamma=\texttt{BT\_SOFT\_POWER}.
$$

- **Probabilità centrale**: \( \bar p(ij)\) (media coerente delle due direzioni posteriori).  
- **Vittorie pesate**:
$$
w(ij)=\bar p(ij)\,n_{\mathrm{eff}},\qquad
w(ji)=n_{\mathrm{eff}}-w(ij).
$$

**BT\_SOFT\_POWER (auto-continua)**  
Se `None`, stima continua \(\gamma \in [1.5,2.1]\) da indicatori di robustezza/coprensività:

$$
\gamma_{\mathrm{auto}} \;=\; 1.5 \;+\; 0.4\,x_1 \;+\; 0.2\,x_2 \;+\; 0.2\,x_3 \;+\; 0.1\,x_4.
$$

**Stima BT (MM con ridge)** — aggiorna \(\pi_i\) da \(w\) e \(n_{\mathrm{eff}}\), ridge \(\lambda=\texttt{LAMBDA\_RIDGE}\), normalizza \(\pi\) a media geometrica 1; proietta \(\theta_i=\log \pi_i\) in \((0,1)\) per ottenere **BT%**.

> Diagnostica: `edges_kept/dropped`, `near_thresh% (±0.10)`, `s_bar_median_kept`, copertura min/med, `HHI_lev(base)`, top-5 leverage, recap dei gate (`N_MIN_BT_TARGET`, `K_used`, `s_min`, `BT_SOFT_POWER`, `harmonic`).

---

## 7) Step 5 — Composito finale (Score_%)

Standardizza `LB` e `BT` → \(z(\mathrm{LB}), z(\mathrm{BT})\).  
Mix e mappatura leggibile:
$$
z_{\mathrm{comp}}=\alpha\,z(\mathrm{LB}) + (1-\alpha)\,z(\mathrm{BT}), \qquad
\mathrm{Score}\_\% \;=\; 100 \cdot \Phi\!\left(\frac{z_{\mathrm{comp}}}{\sqrt{2}}\right).
$$

Tie-break: **LB%**, poi **BT%**, poi **\(N_{\mathrm{eff}}\)/Coverage**.

---

## 8) Colonne in output

`Deck`, `Score_%`, `MAS_%`, `LB_%`, `BT_%`, `SE_%`,  
`N_eff (= \sum_B N_{\mathrm{dir}}(A\to B))`, `Opp_used`, `Opp_total`, `Coverage_%`.

---

## 9) Diagnostica utile

- \(\mathrm{corr}(z(\mathrm{LB}), z(\mathrm{BT}))\) e quota di varianza del mix.  
- Scomposizione `MAS`: contributi \(p(B)\,\hat p(A\to B)\).  
- \(p_{\mathrm{meta}}\) vs \(p_{\mathrm{enc}}\): \(d_{\mathrm{TV}}, \gamma\), top-5 differenze.  
- **BT — sintesi** e **leve**: come nel recap sopra.

---

## 10) Default consigliati (rapidi)

```yaml
MU: 0.5

# Gamma meta (opzionale)
AUTO_GAMMA: true         # se false → usa GAMMA_META_BLEND: 0.30
GAMMA_BASE: 0.10
GAMMA_SLOPE: 1.5
GAMMA_MIN: 0.10
GAMMA_MAX: 0.60

# Smoothing & prudenza
Z_PENALTY: 1.2

# Bradley–Terry
N_MIN_BT_TARGET: 5
BT_SOFT_POWER: null      # null = auto-continuo (~[1.5,2.1]); 1.6 se vuoi fisso
BT_USE_HARMONIC_N: true
BT_NEAR_BAND: 0.10
LAMBDA_RIDGE: 1.5        # 1.0 se vuoi il grafo più "reattivo"

# Composito
ALPHA_COMPOSITE: 0.72
```

---

## 11) Edge case

- Buchi \(A\to B\): pesi rinormalizzati sui validi; se una riga è tutta vuota → `MAS` non informativo (resta **BT**).  
- \(N=0\) o \(WR=\mathrm{NaN}\): la coppia salta in `MAS`; in **BT** la coppia non entra.  
- Nessuna coppia valida per BT: `BT% = 0.5`.  
- Assi non allineati: intersezione prima di tutto.  
- Top-meta assente: \(p_{\mathrm{meta}}\) uniforme, poi blend.

---

## 12) Perché funziona

- **Smoothing**: niente 0/100% finti; \(SE\) e \(LB\) in chiuso.  
- **MAS** meta-aware (gap-recovery + auto-\(\gamma\)): valuta A **contro ciò che incontra davvero**.  
- **BT regolarizzato**: filtro adattivo + **soft-weight armonico** + **ridge** → integra transitività e limita dipendenze da coperture irregolari.  
- **Mix \(\alpha\)**: un’unica leva tra “oggi nel meta” (`LB`) e “talento” (`BT`).

---

## 13) Come leggere i pattern

- `BT%` alto / `MAS%` basso → forte “in assoluto” ma sfavorevole **nel meta attuale**.  
- `MAS%` alto / `BT%` medio → mazzo “giusto per il meta”.  
- `LB% \ll MAS%` → incertezza alta (`SE` alta).  
- `Coverage` bassa → ranking prudente: guarda i breakdown `MAS`.

---

## 14) Mini-workflow di tuning

1. Parti dai default; guarda Top-N, `SE`, `Coverage`, \(\mathrm{corr}(z(\mathrm{LB}), z(\mathrm{BT}))\).  
   **BT**: `near_thresh% ≤ ~15%`, `s_bar_median_kept ≥ ~0.60`, `min/median_opponents_per_deck ≥ 5 / ≥ 11–12`, `HHI_lev(base)` non troppo alto.
2. **Dataset giovane/peloso?** Lascia `BT_SOFT_POWER = null` (auto-cont): si alza da solo se crescono gli edge “al pelo” o l’HHI è concentrato.  
   Se vuoi bloccarlo: imposta **1.6–1.8** in base alla tolleranza al rumore.
3. **Picchi diffusi (non un singolo deck)** → `LAMBDA_RIDGE: 1.0 → 1.5` (smusso globale, ranking più stabile).
4. **Divergenza forte tra dichiarato e giocato** → attiva **AUTO-\(\gamma\)** o regola i parametri di \(\gamma\).
5. **Più “resa oggi”** → `ALPHA_COMPOSITE ↑` (es. `0.72 → 0.75`).  
   **Più “talento intrinseco”** → `ALPHA_COMPOSITE ↓`.

---

## Nota finale su AUTO_K-CV

Procedura **senza manopole**: governa un **`K` unico** per i posteriori (MAS/SE/LB/BT e pesi meta non cambiano), è **robusta** a distribuzioni sbilanciate dei volumi e **reagisce** quando il dataset evolve.  
Log compatti: `K_grid`, `K*`, `K_used`, `beta_auto`, `ΔLL/100`, quantili di `r`, `r_small_median` e **motivo** della scelta.
