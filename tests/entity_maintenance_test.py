"""Prova di audit e fusione delle entita', interamente su SQLite temporaneo."""

import copy
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

RADICE_PROVA = Path(tempfile.mkdtemp(prefix="ares-entity-maintenance-test-"))
os.environ["ARES_TMP"] = str(RADICE_PROVA / "stato")
os.environ["ARES_BACKUP_DIR"] = str(RADICE_PROVA / "backup")

from agno.db.sqlite import SqliteDb  # noqa: E402
from agno.learn.schemas import EntityMemory  # noqa: E402
from agno.learn.utils import build_learning_id  # noqa: E402

from ares import config  # noqa: E402
from ares.backup.snapshots import elenco_snapshot, verifica_snapshot  # noqa: E402
from ares.entities.maintenance import (  # noqa: E402
    ErroreManutenzione,
    analizza,
    applica_piano,
    carica_entita,
    pianifica_fusione,
)
from ares.state.lock import lock_stato  # noqa: E402
from ares.state.stores import namespace_entita  # noqa: E402

UTENTE = "audit"
NAMESPACE = namespace_entita(UTENTE)


def esigi(condizione: object, messaggio: str) -> None:
    if not condizione:
        raise AssertionError(messaggio)


def salva(db: SqliteDb, entita: EntityMemory) -> None:
    learning_id = build_learning_id(
        "entity_memory",
        entity_id=entita.entity_id,
        entity_type=entita.entity_type,
        namespace=NAMESPACE,
    )
    esigi(learning_id is not None, "Agno non ha costruito l'id dell'entita'")
    entita.namespace = NAMESPACE
    db.upsert_learning(
        id=learning_id,
        learning_type="entity_memory",
        entity_id=entita.entity_id,
        entity_type=entita.entity_type,
        namespace=NAMESPACE,
        content=entita.to_dict(),
    )


def riferimenti(candidato) -> frozenset[str]:
    return frozenset((candidato.prima.riferimento, candidato.seconda.riferimento))


def relazione(
    relazione_id: str,
    entity_type: str,
    entity_id: str,
    nome: str,
    direzione: str,
) -> dict:
    return {
        "id": relazione_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "relation": nome,
        "direction": direzione,
    }


def indice_entita(db: SqliteDb) -> dict:
    entita, ignorate = carica_entita(db=db, namespace=NAMESPACE)
    esigi(not ignorate, "righe malformate durante la verifica: " + str(ignorate))
    return {voce.riferimento: voce for voce in entita}


def main() -> int:
    db = SqliteDb(db_file=config.DB_FILE)
    try:
        salva(
            db,
            EntityMemory(
                entity_id="ares_agent",
                entity_type="project",
                name="Ares Agent",
                aliases=["Ares"],
                facts=[{"id": "f1", "content": "Assistente locale costruito con Agno"}],
            ),
        )
        salva(
            db,
            EntityMemory(
                entity_id="assistente_personale_agno",
                entity_type="project",
                name="Assistente personale Agno",
                aliases=["Ares Agent"],
                facts=[{"id": "f2", "content": "Assistente locale costruito con Agno"}],
            ),
        )
        salva(db, EntityMemory(entity_id="atlas", entity_type="company", name="Atlas"))
        salva(db, EntityMemory(entity_id="atlas", entity_type="project", name="Atlas"))
        salva(db, EntityMemory(entity_id="mario_rossi", entity_type="person", name="Mario Rossi"))
        salva(db, EntityMemory(entity_id="mario_rossy", entity_type="person", name="Mario Rossy"))
        salva(db, EntityMemory(entity_id="postgresql", entity_type="product", name="PostgreSQL"))
        salva(db, EntityMemory(entity_id="sistema_alfa", entity_type="system", name="Sistema Alfa"))
        salva(
            db,
            EntityMemory(
                entity_id="vecchio_sistema",
                entity_type="system",
                name="Vecchio Sistema",
                aliases=["Sistema Alfa"],
                archived_at="2026-08-20T10:00:00+00:00",
            ),
        )
        db.upsert_learning(
            id="entity_malformata",
            learning_type="entity_memory",
            entity_id="malformata",
            entity_type="project",
            namespace=NAMESPACE,
            content={"name": "Manca identita'"},
        )

        prima = db.get_learnings(learning_type="entity_memory", namespace=NAMESPACE, limit=None)
        esito = analizza(db=db, namespace=NAMESPACE)
        dopo = db.get_learnings(learning_type="entity_memory", namespace=NAMESPACE, limit=None)

        esigi(prima == dopo, "l'audit ha modificato lo store")
        esigi(len(esito.entita) == 9, "numero di entita' valide inatteso")
        esigi(esito.righe_ignorate == ("entity_malformata",), "riga malformata non segnalata")

        per_coppia = {riferimenti(candidato): candidato for candidato in esito.candidati}
        alias = per_coppia.get(frozenset(("project/ares_agent", "project/assistente_personale_agno")))
        esigi(alias is not None and alias.livello == "forte", "nome/alias condiviso non rilevato")
        esigi(any("alias condiviso" in motivo for motivo in alias.motivi), "manca il motivo dell'alias")

        atlas = per_coppia.get(frozenset(("company/atlas", "project/atlas")))
        esigi(atlas is not None and atlas.livello == "forte", "stesso entity_id tra tipi non rilevato")

        mario = per_coppia.get(frozenset(("person/mario_rossi", "person/mario_rossy")))
        esigi(mario is not None and mario.livello == "possibile", "nomi simili non rilevati")

        archiviata = per_coppia.get(frozenset(("system/sistema_alfa", "system/vecchio_sistema")))
        esigi(archiviata is not None and archiviata.seconda.archiviata, "entita' archiviata non analizzata")

        esigi(
            all("product/postgresql" not in coppia for coppia in per_coppia),
            "entita' priva di indizi segnalata come duplicato",
        )

        completo = analizza(db=db, namespace=NAMESPACE, includi_tutte_le_coppie=True)
        esigi(
            any(c.livello == "manuale" for c in completo.candidati),
            "--all-pairs non aggiunge le coppie da ispezionare a mano",
        )

        ambiente = os.environ.copy()
        comando = subprocess.run(
            [sys.executable, "-m", "ares.entities", "audit", "--user", UTENTE, "--all"],
            cwd=config.BASE_DIR,
            env=ambiente,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        esigi(comando.returncode == 0, "CLI fallita: " + comando.stderr.strip())
        esigi("Entita' analizzate: 9" in comando.stdout, "conteggio assente dalla CLI")
        esigi("Righe malformate ignorate: 1" in comando.stdout, "avviso malformata assente dalla CLI")
        esigi("project/ares_agent" in comando.stdout, "candidato assente dalla CLI")

        finale = db.get_learnings(learning_type="entity_memory", namespace=NAMESPACE, limit=None)
        esigi(prima == finale, "la CLI di audit ha modificato lo store")

        with lock_stato(esclusivo=True):
            bloccato = subprocess.run(
                [sys.executable, "-m", "ares.entities", "audit", "--user", UTENTE],
                cwd=config.BASE_DIR,
                env=ambiente,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        esigi(bloccato.returncode == 2, "l'audit non rispetta il lock esclusivo")
        esigi("stato di Ares" in bloccato.stderr, "il rifiuto del lock non e' spiegato")

        # La fusione rifiuta una scansione incompleta: tolta la riga malformata
        # del test dell'audit, prepariamo un grafo con due copie dello stesso
        # progetto. La sorgente e' archiviata apposta: deve poter essere
        # assorbita, non riattivata.
        db.delete_learning("entity_malformata")
        salva(
            db,
            EntityMemory(
                entity_id="ares_agent",
                entity_type="project",
                name="Ares Agent",
                description="Descrizione canonica",
                aliases=["Ares"],
                properties={"runtime": "Ollama"},
                facts=[
                    {"id": "f1", "content": "Assistente locale costruito con Agno", "source": "chat"},
                    {"id": "f-canon", "content": "Usa modelli locali"},
                ],
                events=[{"id": "e1", "content": "Prima versione completata", "date": "2026-08-20"}],
                relationships=[
                    relazione("r1", "person", "mario_rossi", "works_with", "outgoing"),
                    relazione(
                        "r2",
                        "project",
                        "assistente_personale_agno",
                        "duplicate_of",
                        "outgoing",
                    ),
                ],
            ),
        )
        salva(
            db,
            EntityMemory(
                entity_id="assistente_personale_agno",
                entity_type="project",
                name="Assistente personale Agno",
                description="Descrizione del doppione",
                aliases=["Ares Agent", "Assistente Agno"],
                properties={"runtime": "Altro runtime", "framework": "Agno"},
                facts=[
                    {"id": "f2", "content": "Assistente locale costruito con Agno", "source": "utente"},
                    {"id": "f-source", "content": "Funziona interamente sul computer locale"},
                ],
                events=[
                    {"id": "e2", "content": "Prima versione completata", "date": "2026-08-20"},
                    {"id": "e-source", "content": "Audit delle memorie aggiunto", "date": "2026-08-21"},
                ],
                relationships=[
                    relazione("r3", "person", "mario_rossi", "works_with", "outgoing"),
                    relazione("r4", "project", "ares_agent", "duplicate_of", "incoming"),
                ],
                archived_at="2026-08-21T10:00:00+00:00",
            ),
        )
        salva(
            db,
            EntityMemory(
                entity_id="mario_rossi",
                entity_type="person",
                name="Mario Rossi",
                relationships=[
                    relazione("m1", "project", "ares_agent", "works_with", "incoming"),
                    relazione(
                        "m2",
                        "project",
                        "assistente_personale_agno",
                        "works_with",
                        "incoming",
                    ),
                ],
            ),
        )
        salva(
            db,
            EntityMemory(
                entity_id="sistema_alfa",
                entity_type="system",
                name="Sistema Alfa",
                relationships=[
                    relazione(
                        "s1",
                        "project",
                        "assistente_personale_agno",
                        "monitors",
                        "outgoing",
                    )
                ],
            ),
        )

        prima_fusione = db.get_learnings(learning_type="entity_memory", namespace=NAMESPACE, limit=None)
        entita_fusione, ignorate = carica_entita(db=db, namespace=NAMESPACE)
        esigi(not ignorate, "la riga malformata non e' stata rimossa")
        piano = pianifica_fusione(
            entita=entita_fusione,
            riferimento_sorgente="project/assistente_personale_agno",
            riferimento_canonico="project/ares_agent",
        )
        esigi(piano.sorgente.archiviata, "la sorgente del piano non e' archiviata")
        esigi(piano.statistiche.fatti_aggiunti == 1, "conteggio fatti aggiunti errato")
        esigi(piano.statistiche.fatti_unificati == 1, "fatto duplicato non unificato")
        esigi(piano.statistiche.eventi_aggiunti == 1, "conteggio eventi aggiunti errato")
        esigi(piano.statistiche.eventi_unificati == 1, "evento duplicato non unificato")
        esigi(piano.statistiche.reciproche_aggiunte == 1, "reciproca mancante non ricostruita")
        esigi(piano.statistiche.auto_relazioni_rimosse == 2, "legame fra copie non rimosso")
        esigi(piano.statistiche.conflitti, "conflitti canonici non mostrati")

        con_duplicato_canonico = copy.deepcopy(entita_fusione)
        indice_copie = {voce.riferimento: voce for voce in con_duplicato_canonico}
        indice_copie["project/ares_agent"].entita.facts.append(
            {"id": "f1-bis", "content": "Assistente locale costruito con Agno"}
        )
        piano_dedup = pianifica_fusione(
            entita=con_duplicato_canonico,
            riferimento_sorgente="project/assistente_personale_agno",
            riferimento_canonico="project/ares_agent",
        )
        canonica_dedup = next(
            aggiornamento.dopo
            for aggiornamento in piano_dedup.aggiornamenti
            if aggiornamento.riferimento == "project/ares_agent"
        )
        esigi(len(canonica_dedup["facts"]) == 3, "duplicato gia' canonico non unificato")

        con_id_collisione = copy.deepcopy(entita_fusione)
        indice_collisione = {voce.riferimento: voce for voce in con_id_collisione}
        indice_collisione["project/assistente_personale_agno"].entita.facts.append(
            {"id": "f2", "content": "Contenuto diverso con lo stesso id"}
        )
        try:
            pianifica_fusione(
                entita=con_id_collisione,
                riferimento_sorgente="project/assistente_personale_agno",
                riferimento_canonico="project/ares_agent",
            )
        except ErroreManutenzione as errore:
            esigi("usato per contenuti diversi" in str(errore), "collisione id rifiutata male")
        else:
            esigi(False, "id di fatto riusato per contenuti diversi accettato")
        try:
            pianifica_fusione(
                entita=entita_fusione,
                riferimento_sorgente="company/atlas",
                riferimento_canonico="project/atlas",
            )
        except ErroreManutenzione as errore:
            esigi("tipi incompatibili" in str(errore), "rifiuto fra tipi con motivo inatteso")
        else:
            esigi(False, "fusione fra due tipi reali diversi accettata")
        try:
            pianifica_fusione(
                entita=entita_fusione,
                riferimento_sorgente="project/ares_agent",
                riferimento_canonico="project/assistente_personale_agno",
            )
        except ErroreManutenzione as errore:
            esigi("canonica e' archiviata" in str(errore), "canonica archiviata rifiutata male")
        else:
            esigi(False, "canonica archiviata accettata")
        esigi(
            db.get_learnings(learning_type="entity_memory", namespace=NAMESPACE, limit=None) == prima_fusione,
            "pianificare la fusione ha scritto nello store",
        )

        ambiente = os.environ.copy()
        base_merge = [
            sys.executable,
            "-m",
            "ares.entities",
            "merge",
            "--user",
            UTENTE,
            "--source",
            "project/assistente_personale_agno",
            "--into",
            "project/ares_agent",
        ]
        with lock_stato(esclusivo=False):
            merge_bloccato = subprocess.run(
                [*base_merge, "--apply"],
                cwd=config.BASE_DIR,
                env=ambiente,
                input="FONDI project/assistente_personale_agno IN project/ares_agent\n",
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        esigi(merge_bloccato.returncode == 2, "la fusione non pretende il lock esclusivo")
        esigi(not elenco_snapshot(), "una fusione bloccata ha creato un backup")

        anteprima = subprocess.run(
            base_merge,
            cwd=config.BASE_DIR,
            env=ambiente,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        esigi(anteprima.returncode == 0, "anteprima fallita: " + anteprima.stderr.strip())
        esigi("Anteprima soltanto" in anteprima.stdout, "la CLI non dichiara il dry-run")
        esigi("aggiunge: Funziona interamente" in anteprima.stdout, "fatto aggiunto non mostrato")
        esigi("unifica: Assistente locale" in anteprima.stdout, "fatto unificato non mostrato")
        esigi("person/mario_rossi" in anteprima.stdout, "riga relazionale coinvolta non mostrata")
        esigi(not elenco_snapshot(), "l'anteprima ha creato un backup")
        esigi(
            db.get_learnings(learning_type="entity_memory", namespace=NAMESPACE, limit=None) == prima_fusione,
            "l'anteprima CLI ha modificato lo store",
        )

        annullata = subprocess.run(
            [*base_merge, "--apply"],
            cwd=config.BASE_DIR,
            env=ambiente,
            input="NO\n",
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        esigi(annullata.returncode == 1, "conferma sbagliata non annulla la fusione")
        esigi(not elenco_snapshot(), "una fusione annullata ha creato un backup")

        # Il secondo UPDATE contiene un set, non serializzabile come JSON: il
        # primo UPDATE viene eseguito, il secondo fallisce e SQLAlchemy deve
        # annullare l'intera transazione, cancellazione compresa.
        aggiornamenti_rotti = list(piano.aggiornamenti)
        esigi(len(aggiornamenti_rotti) >= 2, "il piano non tocca abbastanza righe per provare il rollback")
        contenuto_rotto = copy.deepcopy(aggiornamenti_rotti[1].dopo)
        contenuto_rotto["aliases"] = {"non", "serializzabile"}
        aggiornamenti_rotti[1] = replace(aggiornamenti_rotti[1], dopo=contenuto_rotto)
        piano_rotto = replace(piano, aggiornamenti=tuple(aggiornamenti_rotti))
        try:
            applica_piano(db=db, piano=piano_rotto)
        except ErroreManutenzione:
            pass
        else:
            esigi(False, "il guasto a meta' transazione non e' stato rilevato")
        esigi(
            db.get_learnings(learning_type="entity_memory", namespace=NAMESPACE, limit=None) == prima_fusione,
            "il rollback ha lasciato una fusione parziale",
        )

        prima_inattesa = copy.deepcopy(piano.aggiornamenti[0].prima)
        prima_inattesa["description"] = "stato che il piano non ha letto"
        piano_obsoleto = replace(
            piano,
            aggiornamenti=(
                replace(piano.aggiornamenti[0], prima=prima_inattesa),
                *piano.aggiornamenti[1:],
            ),
        )
        try:
            applica_piano(db=db, piano=piano_obsoleto)
        except ErroreManutenzione as errore:
            esigi("stato cambiato" in str(errore), "piano obsoleto rifiutato con motivo inatteso")
        else:
            esigi(False, "un piano costruito su uno stato diverso e' stato applicato")
        esigi(
            db.get_learnings(learning_type="entity_memory", namespace=NAMESPACE, limit=None) == prima_fusione,
            "il rifiuto del piano obsoleto ha modificato lo store",
        )

        conferma = "FONDI project/assistente_personale_agno IN project/ares_agent\n"
        applicata = subprocess.run(
            [*base_merge, "--apply"],
            cwd=config.BASE_DIR,
            env=ambiente,
            input=conferma,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        esigi(applicata.returncode == 0, "fusione CLI fallita: " + applicata.stderr.strip())
        esigi("Fusione completata e verificata" in applicata.stdout, "verifica finale non annunciata")

        indice = indice_entita(db)
        esigi("project/assistente_personale_agno" not in indice, "sorgente ancora presente")
        canonica = indice["project/ares_agent"].entita
        esigi("Assistente personale Agno" in canonica.aliases, "nome sorgente non trasferito negli alias")
        esigi("Assistente Agno" in canonica.aliases, "alias sorgente non trasferito")
        esigi(canonica.description == "Descrizione canonica", "descrizione canonica sovrascritta")
        esigi(canonica.properties == {"runtime": "Ollama", "framework": "Agno"}, "proprieta' fuse male")
        esigi(len(canonica.facts) == 3, "fatti non unificati")
        esigi(len(canonica.events) == 2, "eventi non unificati")
        for voce in indice.values():
            esigi(
                all(
                    not (
                        relazione_salvata.get("entity_type") == "project"
                        and relazione_salvata.get("entity_id") == "assistente_personale_agno"
                    )
                    for relazione_salvata in (voce.entita.relationships or [])
                ),
                "relazione rimasta verso la sorgente su " + voce.riferimento,
            )
        esigi(len(canonica.relationships) == 2, "relazioni canoniche non deduplicate")
        esigi(len(indice["person/mario_rossi"].entita.relationships) == 1, "reciproca Mario duplicata")
        esigi(
            any(r.get("relation") == "monitors" and r.get("direction") == "incoming" for r in canonica.relationships),
            "reciproca monitors non ricostruita",
        )

        snapshot = elenco_snapshot()
        esigi(len(snapshot) == 1, "la fusione non ha creato un solo backup")
        manifest = verifica_snapshot(snapshot[0], percorso_diretto=True)
        esigi(manifest.get("type") == "pre-merge", "snapshot non marcato pre-merge")
        db_backup = SqliteDb(db_file=str(snapshot[0] / "kairos.db"))
        esigi(
            db_backup.get_learning(
                learning_type="entity_memory",
                entity_id="assistente_personale_agno",
                entity_type="project",
                namespace=NAMESPACE,
            )
            is not None,
            "il backup non contiene la sorgente precedente",
        )
        print("ok       lettura completa     - 9 entita' valide e una malformata segnalata")
        print("ok       criteri              - alias, id, somiglianza e archiviati")
        print("ok       controllo manuale    - --all-pairs include le coppie senza indizi")
        print("ok       sola lettura         - righe identiche prima e dopo API e CLI")
        print("ok       CLI                  - inventario e candidati leggibili")
        print("ok       lock                 - audit e fusione rispettano lock condiviso/esclusivo")
        print("ok       piano fusione        - contenuti, conflitti e grafo calcolati senza scritture")
        print("ok       anteprima/conferma   - nessun backup o modifica prima della frase esatta")
        print("ok       rollback             - guasto sul secondo UPDATE, archivio invariato")
        print("ok       fusione              - sorgente rimossa e canonica verificata")
        print("ok       relazioni            - riferimenti riscritti, deduplicati e reciproci")
        print("ok       backup pre-merge     - snapshot valido con la sorgente originale")
        return 0
    except Exception as errore:
        print("FALLITO -", type(errore).__name__ + ":", errore)
        return 1
    finally:
        shutil.rmtree(RADICE_PROVA, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
