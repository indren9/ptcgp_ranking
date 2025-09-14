# CONTRIBUTING.md — ptcgp_ranking

Grazie per il tuo interesse! Questo progetto è pubblicato da **Andrea Visentin** con licenza **MIT**.  
Per proporre modifiche o idee segui i passaggi sotto. Manteniamo il flusso semplice e amichevole.

## 1) Come proporre cambiamenti
**Preferito:** *fork → branch → Pull Request (PR)*
1. Fai il **fork** della repo su GitHub.
2. **Clona** il tuo fork in locale ed entra nella cartella.
3. Crea un **branch** descrittivo (es. `fix/wr-symmetry` o `feat/auto-k`).
4. Fai commit piccoli e chiari (vedi §3) e **push** sul tuo fork.
5. Apri una **Pull Request** verso `main` della repo originale, spiegando **cosa** e **perché**.

> Se sei collaboratore della repo principale, evita il push diretto su `main`: usa comunque branch + PR.

## 2) Ambiente di sviluppo (setup rapido)
- Python 3.10+ consigliato.
- Crea un ambiente virtuale e installa le dipendenze:
  ```bash
  python -m venv .venv
  # Windows
  .venv\Scripts\activate
  # macOS/Linux
  source .venv/bin/activate

  pip install -U pip
  pip install -r requirements.txt
  ```
- Apri i notebook/py in VS Code. Se usi i notebook, **evita di committare output pesanti**; per grafici grandi salva su `outputs/` (già ignorato dal `.gitignore`).

## 3) Stile di codice e commit
- **PEP8** + **type hints** dove sensato.
- Nomi chiari, funzioni pure dove possibile, logging ragionato (livello **INFO** per i passaggi chiave).
- **Commit**: messaggi in forma imperativa e breve (es. `Fix: impone simmetria WR` / `Feat: AUTO_K con CV`).  
  Facoltativo: schema *Conventional Commits* (`feat:`, `fix:`, `docs:`…).

## 4) Dati, marchi e sicurezza
- **Non** includere nel repo dati proprietari o derivazioni non consentite dai ToS di terzi (es. dump completi). Mantieni solo **codice** e **contratti di output** minimi.
- **Mai** committare credenziali/token (le cartelle `outputs/`, `cache/` e simili sono già ignorate).
- I marchi citati (es. Pokémon™) restano dei rispettivi proprietari; il progetto è non affiliato (vedi `NOTICE`).

## 5) Licenza dei contributi
Inviando una PR accetti che il tuo contributo sia rilasciato sotto la **licenza MIT** del progetto, a beneficio di tutti gli utenti.

## 6) Linee guida per PR
- Descrivi il **problema** e come la PR lo risolve.
- Aggiungi screenshot/log **minimali** se utile alle diagnostiche.
- Mantieni le PR **coese** (piccole > grandi). Se tocchi più moduli indipendenti, valuta PR separate.
- Spunta la checklist:
  - [ ] Il codice esegue localmente.
  - [ ] Non introduce regressioni evidenti sui notebook/contratti Parte 1 (scraping+prep) e Parte 2 (MARS).
  - [ ] Non aggiunge file pesanti o dati di terzi al repository.
  - [ ] Rispetta lo stile del progetto (nomi chiari, PEP8, logging essenziale).

## 7) Domande o problemi
Apri una **Issue** su GitHub: descrivi in poche righe il contesto, i passi per riprodurre e l’output atteso.

Buon lavoro e grazie per il contributo!
