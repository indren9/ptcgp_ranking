# MARS P2 — WP2 R3 · Patch candidata isolata

> **HISTORY — 25/11/2026.** Original WP2 candidate note retained for traceability. Its STOP statements and implementation status describe that session, not the accepted local WP4 deployment of 27/11/2026. The operative private completed-set path now uses `storage/tcg_private_set_layout.py` and `storage/tcg_private_raw.py`; the original flat-layout planner/publisher and `patches/apply_private_job_patch.py` are historical candidate materials, not operational instructions. Do not run the patcher against the deployed job. See [production addendum](docs/ptcg_live_production.md#p2-local-private-operation--2026-11-27) and [RAW contract](docs/tcg-private-local-raw-contract.md).

**Data:** 2026-11-25 · **Baseline autoritativa:** `indren9/ptcgp_ranking` main commit `d39c7eca97c8e0eb85fda770085f77fb4a13674b` · **Owner GO:** WRITE locale isolato soltanto.

**Stato:** PARTIAL / IMPLEMENTATION CANDIDATE. Nessun test, acquisizione LIVE, CI, scheduler, OneDrive, modifica di `main`, Git remoto o deploy eseguiti.

## Contenuto

- `scripts/tcg_live_provisional_current.py`: lettura/riconciliazione fail-closed delle evidenze dell'observer ufficiale (review+receipt+observations immutabili); codice `TMP-PTCGL-...` stabile; staging di catalogo/YAML in directory esterna al repository; entrypoint LIVE esplicito **non eseguito**.
- `scripts/tcg_live_identity_bind.py`: match stretto nome→codice da **snapshot del catalogo carte Limitless**; staged catalog con codice vero e ricevuta immutabile. NON è un parser verificato sulla pagina corrente, non è una promozione canonica e non modifica le vecchie prove.
- `scripts/tcg_live_private_plan.py`: confronto *read-only* fra l'ultima finestra completata e il manifest del bundle OneDrive privato; separato dallo stato pubblico GitHub.
- `storage/tcg_private_guarded.py`: pubblicatore candidato con backup di tutti e tre gli artefatti, journaling e rollback su eccezione; crash-hard → journal pending e recovery esplicita. **Non garantisce atomicità multi-file agli attuali consumer senza loro verifica dei manifest**.
- `patches/apply_private_job_patch.py`: integrazione condizionata da blob SHA esatto nel job TCG, con nuovo `--publish-private` distinto da `--publish --allow-public-write`. Il patcher è un supporto di applicazione, NON eseguito.
- `tests/test_tcg_live_p2_r3_candidate.py`: prime fixture e test per WP3, scritti ma NON eseguiti.

## Condizioni di sicurezza

1. `TMP` vale **solo nello staging CURRENT**. Non è fonte di verità, non abilita `latest_completed` o l'output pubblico GitHub.
2. Tutti i confini temporali devono provenire da annunci ufficiali verificabili; **mai** dall'uscita cartacea. I tornei si acquisiscono esclusivamente dalla **Limitless Tournament API**, niente fallback HTML.
3. Non si riscrivono CSV, manifest o hash precedenti durante il binding. Il codice canonico passa da **un nuovo run** separato, la cui fattibilità end-to-end richiede WP3.
4. Il publisher storico OneDrive sostituisce file individualmente: il modulo guardato conserva l'ultimo bundle come backup e supporta rollback, ma **serve fault injection e una policy reader-side** per dimostrare la sicurezza sotto crash. È vietato attivarlo in WP2.
5. Il controller scheduler delle 10:15 e le sorgenti GitHub remote non sono stati modificati. Prima di WP4 serviranno integrazione del runner locale e test isolati.

## Workflow di applicazione previsto (NON eseguito)

In un checkout/worktree isolato verificato della baseline indicata, copiare i tre nuovi moduli nei rispettivi percorsi, inserire le fixture in `tests/`, e usare il patcher *solo dopo* controllo di `git status` e del Git blob SHA target. Il patcher verifica che tutti gli anchor siano unici e scrive **soltanto** `scripts/tcg_live_latest_completed_job.py` nel checkout isolato. Eseguire successivamente diff statico e verifica sintattica mirata nel limite del mandato. **Non** avviare alcun `--run-live` o `--publish-private`: questi sono percorsi per WP3/4.

## LIMITATIONS / GAP verso WP2 PASS

- Desktop Commander PC-Andrea risulta online ma non ha restituito ancora preflight `get_config`/filesystem: precedente sessione aveva chiamate pendenti. Nessuna scrittura locale sul PC effettuata.
- Il pacchetto è un **candidato di integrazione**, non una patch applicata al repository. Manca verifica reale del source HTML Limitless per il binding e delle forme effettive dei report official observer.
- La compatibilità completa della pipeline CURRENT con `TMP` e i percorsi di output non è verificata in runtime; nessuna promozione QA.
- La modalità completed privata non modifica automaticamente il catalogo canonico; deve passare attraverso un catalogo tecnicamente autorizzato con codici verificati.
- Mancano test di recupero dopo hard-crash, concurrency, linkage del launcher delle 10:15 e consumer-side manifest gate; tutti restano STOP fino al relativo GO.

**SESSION CLOSE:** codice candidato isolato con provenienza, file+SHA nel manifest ZIP; test NON eseguiti, produzione invariata. Prossima fase: ripresa dispositivo con readback, applicazione in worktree isolato e verifica statica prima di gate WP3. `MERGE/PRODUCTION = STOP`.

**Nota Session 3 (25/11, ripresa):** corretto un mismatch del candidato precedente: il report reale `Store.save` non incorpora `observations`; il runner ora recupera i record da `Store.latest()` solo quando il report proviene da una review immutabile con receipt associata. Non sono stati eseguiti test; integrazione e copertura restano WP3.
