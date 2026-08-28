"""
Schemi personalizzati per gli store di apprendimento
====================================================
Agno serializza le classi schema per percorso di import, quindi devono
vivere in un modulo importabile. Definirle dentro `__main__` le fa
sopravvivere al processo corrente ma non al round-trip su database.

Gli store sono dataclass, non modelli Pydantic: i campi si dichiarano con
`field(default=..., metadata={"description": ...})`. La descrizione finisce
nel prompt di estrazione, quindi e' l'unica leva che hai per dire al modello
cosa vuoi in quel campo. Scrivile come istruzioni, non come etichette.

**I campi sono stringhe, e non e' una scelta di stile.** Agno costruisce lo
strumento `update_profile` dai campi dello schema ma annota ognuno come
`Optional[str]`, con il commento "Simplified to str for LLM compatibility"
(`agno/learn/stores/user_profile.py`). Un campo dichiarato `List[str]` non
diventa mai una lista: il modello puo' solo passare una stringa, e le
dataclass non validano, quindi quella stringa finisce in archivio sotto un
tipo che nessuno rispetta. Qui c'erano `expertise` e `tools_and_stack`
dichiarati `List[str]` e riletti dal database come `str`. Chiedere l'elenco
nella descrizione e' l'unico modo di ottenerne uno.

**Il contesto di sessione non si estende.** Qui vivevano anche un
`AresSessionContext` con `blockers` e `decisions`, che il modello non ha
mai potuto scrivere: `save_session_context` ha una firma fissa - summary,
goal, plan, progress - costruita a mano e non dallo schema
(`agno/learn/stores/session_context.py`). Lo schema serve solo a rileggere
cio' che quella firma ha scritto. Erano due campi che nessun percorso poteva
riempire, come l'embedder di ingestion prima di loro, e sono stati rimossi.

**Uno schema serve anche a cambiare come una cosa viene resa**, non solo a
aggiungere campi: `AresMemories` non porta nessun campo nuovo, sovrascrive
il metodo con cui le memorie diventano testo per il prompt.
"""

from dataclasses import dataclass, field
from typing import Optional

from agno.learn.schemas import Memories, UserProfile


@dataclass
class AresProfile(UserProfile):
    """Profilo utente esteso con i campi che contano per un assistente personale."""

    timezone: Optional[str] = field(
        default=None,
        metadata={"description": "Fuso orario dell'utente, per esempio Europe/Rome"},
    )
    language: Optional[str] = field(
        default=None,
        metadata={"description": "Lingua in cui l'utente preferisce ricevere le risposte"},
    )
    occupation: Optional[str] = field(
        default=None,
        metadata={"description": "Lavoro o ruolo professionale dell'utente"},
    )
    expertise: Optional[str] = field(
        default=None,
        metadata={
            "description": (
                "Ambiti in cui l'utente e' competente, separati da virgola. Serve a "
                "calibrare il livello di dettaglio: non spiegare le basi di cio' che "
                "l'utente padroneggia."
            )
        },
    )
    communication_style: Optional[str] = field(
        default=None,
        metadata={
            "description": (
                "Come l'utente vuole le risposte: lunghezza, tono, uso di esempi, "
                "se preferisce codice o prosa"
            )
        },
    )
    tools_and_stack: Optional[str] = field(
        default=None,
        metadata={
            "description": (
                "Strumenti, linguaggi, hardware e servizi che l'utente usa "
                "abitualmente, separati da virgola. Serve a dare risposte gia' "
                "adattate al suo ambiente."
            )
        },
    )
    current_focus: Optional[str] = field(
        default=None,
        metadata={"description": "Su cosa l'utente sta lavorando in questo periodo"},
    )


@dataclass
class AresMemories(Memories):
    """Memorie che portano con se' la data, invece di arrivare senza tempo.

    Ogni memoria ha `created_at` e `updated_at` in archivio da sempre: e' il
    rendering a scartarli. `Memories.get_memories_text` costruisce le righe
    con il solo `content`, quindi l'agente riceve cio' che sa senza sapere da
    quando lo sa. Una preferenza dichiarata l'anno scorso e una di ieri
    arrivano identiche, e non c'e' modo di accorgersi che una e' vecchia.

    Il metodo e' l'unico punto da toccare: lo store lo chiama su qualunque
    schema gli sia stato configurato (`to_context` fa
    `data.get_memories_text()`, e ogni costruzione passa da
    `self.config.schema or Memories`). Nessuna patch, nessun campo aggiunto -
    aggiungerne uno qui sarebbe inutile, perche' le operazioni sulle memorie
    lavorano sulla lista e non sui campi dello schema.

    La data resa e' `updated_at`, cioe' l'ultima volta che quella memoria e'
    stata confermata o riscritta, con `created_at` come ripiego. Assoluta e
    non relativa: l'ora corrente sta nello stesso prompt, e far calcolare
    "tre mesi fa" a un 9B aggiunge aritmetica senza aggiungere informazione.
    """

    def get_memories_text(self) -> str:
        """Le memorie come testo per il prompt, ognuna con la sua data.

        La legenda in testa non e' decorazione: una data fra parentesi quadre
        e basta si presta a essere letta come parte di cio' che l'utente ha
        detto. Il blocco che avvolge queste righe e' scritto da Agno, in
        inglese e non modificabile, quindi la spiegazione puo' stare solo qui.
        """
        if not self.memories:
            return ""

        righe = []
        for memoria in self.memories:
            if not isinstance(memoria, dict):
                righe.append("- " + str(memoria))
                continue
            contenuto = memoria.get("content")
            if not contenuto:
                continue
            quando = (memoria.get("updated_at") or memoria.get("created_at") or "")[:10]
            righe.append("- " + contenuto + (" [" + quando + "]" if quando else ""))

        if not righe:
            return ""
        return "\n".join(["(fra parentesi quadre, la data in cui hai saputo la cosa)"] + righe)
