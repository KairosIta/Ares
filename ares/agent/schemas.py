"""Schemi personalizzati per gli store di apprendimento.

Agno serializza gli schemi per percorso di import: devono vivere in un
modulo importabile, non in `__main__`.

La `description` nei metadata di ogni campo finisce nel prompt di
estrazione: scrivila come istruzione al modello, non come etichetta.

Vincoli di Agno da conoscere prima di cambiare qualcosa:

- **I campi del profilo sono stringhe.** Agno annota ogni parametro di
  `update_profile` come `Optional[str]`, e le dataclass non validano: un
  `List[str]` arriverebbe come stringa. Per un elenco, chiedilo nella
  descrizione.
- **Il contesto di sessione non si estende.** `save_session_context` ha una
  firma fissa (summary, goal, plan, progress) che non deriva dallo schema.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agno.learn.schemas import Memories, UserProfile


@dataclass
class AresProfile(UserProfile):
    """Profilo utente esteso con i campi che contano per un assistente personale."""

    timezone: str | None = field(
        default=None,
        metadata={"description": "Fuso orario dell'utente, per esempio Europe/Rome"},
    )
    language: str | None = field(
        default=None,
        metadata={"description": "Lingua in cui l'utente preferisce ricevere le risposte"},
    )
    occupation: str | None = field(
        default=None,
        metadata={"description": "Lavoro o ruolo professionale dell'utente"},
    )
    expertise: str | None = field(
        default=None,
        metadata={
            "description": (
                "Ambiti in cui l'utente e' competente, separati da virgola. Serve a "
                "calibrare il livello di dettaglio: non spiegare le basi di cio' che "
                "l'utente padroneggia."
            )
        },
    )
    communication_style: str | None = field(
        default=None,
        metadata={
            "description": (
                "Come l'utente vuole le risposte: lunghezza, tono, uso di esempi, se preferisce codice o prosa"
            )
        },
    )
    tools_and_stack: str | None = field(
        default=None,
        metadata={
            "description": (
                "Strumenti, linguaggi, hardware e servizi che l'utente usa "
                "abitualmente, separati da virgola. Serve a dare risposte gia' "
                "adattate al suo ambiente."
            )
        },
    )
    current_focus: str | None = field(
        default=None,
        metadata={
            "description": (
                "Progetti e obiettivi attuali dell'utente, conservando lo stato dichiarato: "
                "in valutazione, deciso, programmato o iniziato. Una decisione non implica "
                "lavoro gia' in corso; un avvio sconosciuto non significa che il lavoro non "
                "sia iniziato. Mantieni un avvio gia' confermato se viene solo ribadito l'obiettivo."
            )
        },
    )


@dataclass
class AresMemories(Memories):
    """Memorie che non si perdono quando vengono corrette, rese nel prompt con la loro data.

    Una memoria cancellata o riscritta con un contenuto diverso non sparisce:
    una copia passa in `superate`, con `invalidata_il` e, per una
    riscrittura, `sostituita_da` (l'id della voce valida, che resta lo
    stesso). `memories` contiene solo le valide, quindi il prompt, l'estrattore
    e l'eco non le vedono; `/memorie superate` le mostra.

    `Memories.get_memories_text` usa solo `content`: il modello non saprebbe
    se una preferenza e' di ieri o dell'anno scorso. La data e' `updated_at`
    (ripiego `created_at`), assoluta: l'ora corrente e' gia' nel prompt, e
    un calcolo relativo sarebbe aritmetica in piu' per un modello piccolo.
    """

    superate: list[dict[str, Any]] = field(default_factory=list, metadata={"internal": True})

    def _supera(self, voce: dict[str, Any], sostituita_da: str | None) -> None:
        copia = {**voce, "invalidata_il": datetime.now(UTC).isoformat()}
        if sostituita_da is not None:
            copia["sostituita_da"] = sostituita_da
        self.superate.append(copia)

    def update_memory(self, memory_id: str, content: str, **kwargs: Any) -> bool:
        voce = self.get_memory(memory_id)
        if voce is not None and (voce.get("content") or "").strip() != content.strip():
            self._supera(voce, sostituita_da=memory_id)
        return super().update_memory(memory_id, content, **kwargs)

    def __setattr__(self, nome: str, valore: Any) -> None:
        # Agno cancella riassegnando `memories` filtrata, nello strumento di
        # estrazione come in `delete_memory`: e' l'unico punto da cui passa
        # ogni cancellazione. Il costruttore assegna prima che il campo esista.
        if nome == "memories" and "memories" in self.__dict__ and isinstance(valore, list):
            restano = {v.get("id") for v in valore if isinstance(v, dict)}
            for voce in self.__dict__["memories"] or []:
                if isinstance(voce, dict) and voce.get("id") not in restano:
                    self._supera(voce, sostituita_da=None)
        super().__setattr__(nome, valore)

    def get_memories_text(self) -> str:
        """Le memorie come testo per il prompt, ognuna con la sua data.

        La legenda in testa evita che la data sia letta come parte di cio'
        che l'utente ha detto.
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
        return "\n".join(["(fra parentesi quadre, la data in cui hai saputo la cosa)", *righe])


@dataclass
class AresMemorieSenzaData(AresMemories):
    """Come `AresMemories`, ma nel prompt senza la data: con `DATE_MEMORIE` spento."""

    def get_memories_text(self) -> str:
        return Memories.get_memories_text(self)
