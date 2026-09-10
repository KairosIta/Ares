"""Verifica offline che la valutazione non produca successi senza prove."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals import memory_quality as mq


def risposta(valore=None, certezza="sconosciuto", evidenza=None):
    return json.dumps({"valore": valore, "certezza": certezza, "evidenza": evidenza})


def memoria(testo):
    return {"memorie": {"memories": [{"content": testo}]}}


class QualitaMemoriaTest(unittest.TestCase):
    def test_citazioni_negative_o_ritagliate_non_superano(self):
        fase = mq.CASI["avvio"][2]
        for testo in (
            "Non ha iniziato a lavorare a ORIONE-42.",
            "Forse ha iniziato a lavorare a ORIONE-42.",
            "Se ha iniziato a lavorare a ORIONE-42, lo dira'.",
            "E' falso che ha iniziato a lavorare a ORIONE-42.",
        ):
            for citazione in (testo, "iniziato", "ha iniziato a lavorare a ORIONE-42"):
                with self.subTest(testo=testo, citazione=citazione):
                    self.assertEqual(
                        mq.valuta(
                            fase, risposta("iniziato", "confermato", citazione), memoria(testo), memoria("ORIONE-42")
                        )["stato"],
                        "da_revisionare",
                    )

    def test_citazione_breve_o_contraddetta_da_un_altro_ricordo_richiede_revisione(self):
        fase = mq.CASI["recupero"][0]
        testo = "Il progetto si chiama AURORA-73."
        self.assertEqual(
            mq.valuta(fase, risposta("AURORA-73", "confermato", "AURORA-73"), memoria(testo))["stato"], "da_revisionare"
        )
        stato = {"profilo": {"current_focus": "Il progetto non si chiama AURORA-73."}, **memoria(testo)}
        self.assertEqual(mq.valuta(fase, risposta("AURORA-73", "confermato", testo), stato)["stato"], "da_revisionare")

    def test_conservazione_richiede_avvio_precedente_affermativo_del_progetto(self):
        fase = mq.CASI["avvio"][3]
        dopo = "Ha iniziato a lavorare a ORIONE-42."
        for testo in (
            "Ha deciso di realizzare ORIONE-42.",
            "Non ha iniziato a lavorare a ORIONE-42.",
            "Forse ha iniziato a lavorare a ORIONE-42.",
            "Ha pianificato l'avvio di ORIONE-42 per domani.",
            "Ha iniziato a valutare ORIONE-42.",
            "Ha deciso ORIONE-42. Ha iniziato a lavorare a VEGA-19.",
        ):
            with self.subTest(testo=testo):
                self.assertEqual(
                    mq.valuta(fase, risposta("iniziato", "confermato", dopo), memoria(dopo), memoria(testo))["stato"],
                    "non_conclusivo",
                )

    def test_interrupt_conserva_fasi_gia_scritte(self):
        percorsi = []

        def interrompi(comando, **kwargs):
            percorso = Path(comando[-1])
            percorsi.append(percorso)
            mq.scrivi_json(percorso, {"fasi": [{"nome": "iniziale", "valutazione": {"stato": "superato"}}]})
            raise KeyboardInterrupt

        with patch.object(mq.subprocess, "run", side_effect=interrompi), self.assertRaises(KeyboardInterrupt) as evento:
            mq.esegui_caso("correzione", 1, 180)
        risultato = getattr(evento.exception, "risultato", None)
        self.assertIsNotNone(risultato, "l'interruzione ha perso il rapporto parziale")
        self.assertEqual(mq.aggrega([risultato])["superato"], 1)
        self.assertEqual(mq.aggrega([risultato])["errore"], 1)
        self.assertFalse(percorsi[0].parent.exists())

    def test_decisione_e_futuro_non_confermano_ne_avvio_ne_mancato_avvio(self):
        for fase in mq.CASI["avvio"][:2]:
            for valore in ("iniziato", "non iniziato"):
                with self.subTest(fase=fase.nome, valore=valore):
                    self.assertEqual(
                        mq.valuta(fase, risposta(valore, "confermato", valore), memoria(valore))["stato"],
                        "fallito",
                    )

    def test_risposta_incerta_non_nasconde_avvio_inventato_nella_memoria(self):
        for testo in ("Sta realizzando ORIONE-42.", "Non ha iniziato ORIONE-42.", "Avvio non confermato."):
            self.assertEqual(mq.valuta(mq.CASI["avvio"][0], risposta(), memoria(testo))["stato"], "da_revisionare")

    def test_avvio_confermato_resta_recuperabile_dopo_una_decisione_ribadita(self):
        for fase in mq.CASI["avvio"][2:]:
            testo = "Ha iniziato a lavorare a ORIONE-42."
            prima = memoria(testo if fase.avvio_precedente else "Il progetto e' ORIONE-42.")
            dopo = memoria(testo)
            self.assertEqual(mq.valuta(fase, risposta(), dopo, prima)["stato"], "fallito")
            self.assertEqual(
                mq.valuta(fase, risposta("iniziato", "confermato", testo), dopo, prima)["stato"], "superato"
            )

    def test_avvio_citato_in_forma_equivalente_e_una_prova(self):
        """La citazione che dice l'avvio senza dire "iniziato" sostiene il valore.

        E' il testo che il modello ha prodotto davvero: la memoria unisce la
        decisione e l'avvio in una frase, e la sonda cita la seconda meta'.
        Prima della dichiarazione sulla fase questo usciva non conclusivo,
        cioe' una risposta corretta con la sua citazione verbatim non contava
        ne' come successo ne' come difetto.
        """
        durevole = (
            "Ha deciso di realizzare ORIONE-42, il suo unico progetto personale attuale; "
            "ha confermato l'avvio dei lavori."
        )
        citazione = "ha confermato l'avvio dei lavori"
        for fase in mq.CASI["avvio"][2:]:
            prima = memoria(
                "Ha iniziato a lavorare a ORIONE-42." if fase.avvio_precedente else "Il progetto e' ORIONE-42."
            )
            with self.subTest(fase=fase.nome):
                esito = mq.valuta(fase, risposta("iniziato", "confermato", citazione), memoria(durevole), prima)
                self.assertEqual(esito["stato"], "superato")

    def test_la_forma_equivalente_non_scavalca_le_altre_guardie(self):
        """L'equivalenza dice come si puo' citare, non sostituisce le prove.

        Restano da superare: la citazione deve stare negli store, il contesto
        originale non deve negare, e tre parole sono il minimo perche' una
        citazione sostenga da se'.
        """
        fase = mq.CASI["avvio"][2]
        citazione = "ha confermato l'avvio dei lavori"
        sonda = risposta("iniziato", "confermato", citazione)
        prima = memoria("Il progetto e' ORIONE-42.")

        # Non negli store: la sonda cita una frase che nessuno ha scritto.
        self.assertEqual(
            mq.valuta(fase, sonda, memoria("Ha deciso di realizzare ORIONE-42."), prima)["stato"], "non_conclusivo"
        )
        # Negata nel contesto originale.
        for testo in (
            "Non ha confermato l'avvio dei lavori.",
            "Forse ha confermato l'avvio dei lavori.",
            "Non e' vero che ha confermato l'avvio dei lavori.",
        ):
            with self.subTest(testo=testo):
                self.assertEqual(mq.valuta(fase, sonda, memoria(testo), prima)["stato"], "da_revisionare")
        # Troppo breve per sostenere da se'.
        self.assertEqual(
            mq.valuta(fase, risposta("iniziato", "confermato", "l'avvio"), memoria("Conferma l'avvio."), prima)[
                "stato"
            ],
            "non_conclusivo",
        )

    def test_gli_altri_casi_continuano_a_pretendere_il_termine(self):
        """L'equivalenza vale dove e' dichiarata, non per tutte le fasi.

        `recupero` non dichiara nulla, quindi una citazione che parla del
        dato senza contenerlo resta non conclusiva come prima: la correzione
        non ha allentato la regola generale.
        """
        fase = mq.CASI["recupero"][0]
        self.assertIsNone(fase.evidenza_equivalente)
        testo = "Il progetto personale ha un nome in codice."
        esito = mq.valuta(fase, risposta("AURORA-73", "confermato", testo), memoria(testo))
        self.assertEqual(esito["stato"], "non_conclusivo")

    def test_ogni_fase_di_ogni_caso_finisce_nel_rapporto(self):
        """Il rapporto deve poter contenere qualunque fase dichiarata.

        Il campo `evidenza_equivalente` e' un'espressione compilata, che
        `json.dumps` non sa scrivere: senza conversione il worker moriva con
        codice 1 mentre salvava, e non a fine caso ma alla prima fase che la
        dichiarava - perdendo anche le fasi gia' misurate. Le prove sul
        verdetto non lo vedevano, perche' non passano dal rapporto.
        """
        for nome, fasi in mq.CASI.items():
            for fase in fasi:
                with self.subTest(caso=nome, fase=fase.nome):
                    dati = mq.dialogo_serializzabile(fase)
                    json.dumps(dati)
                    atteso = fase.evidenza_equivalente.pattern if fase.evidenza_equivalente else None
                    self.assertEqual(dati["evidenza_equivalente"], atteso)

    def test_dato_inventato_fallisce(self):
        fase = mq.CASI["ipotesi"][0]
        esito = mq.valuta(fase, risposta("Milano", "confermato", "Milano"), {})
        self.assertEqual(esito["stato"], "fallito")
        self.assertEqual(mq.valuta(fase, risposta(), {})["stato"], "superato")

    def test_ipotesi_in_memoria_richiede_revisione(self):
        fase = mq.CASI["ipotesi"][0]
        for testo in ("Potrebbe trasferirsi a Milano.", "Abita a Milano."):
            with self.subTest(testo=testo):
                self.assertEqual(mq.valuta(fase, risposta(), memoria(testo))["stato"], "da_revisionare")

    def test_recupero_richiede_evidenza_persistente(self):
        fase = mq.CASI["recupero"][0]
        testo = "Il progetto si chiama AURORA-73."
        sonda = risposta("AURORA-73", "confermato", testo)
        self.assertEqual(mq.valuta(fase, sonda, {})["stato"], "non_conclusivo")
        self.assertEqual(mq.valuta(fase, sonda, memoria(testo))["stato"], "superato")
        self.assertEqual(mq.valuta(fase, risposta(), memoria(testo))["stato"], "fallito")

    def test_source_e_contesto_sessione_non_sono_prove_durevoli(self):
        fase = mq.CASI["recupero"][0]
        stato = {"memorie": {"memories": [{"source": "AURORA-73"}]}, "contesto": {"summary": "AURORA-73"}}
        self.assertEqual(
            mq.valuta(fase, risposta("AURORA-73", "confermato", "AURORA-73"), stato)["stato"],
            "non_conclusivo",
        )

    def test_markdown_e_data_del_renderer(self):
        fase = mq.CASI["recupero"][0]
        testo = "Il progetto si chiama AURORA-73. [2026-09-07]"
        sonda = "```json\n" + risposta("AURORA-73", "confermato", testo) + "\n```"
        stato = {**memoria("Il progetto si chiama AURORA-73."), "contesto_durevole": testo}
        self.assertEqual(mq.valuta(fase, sonda, stato)["stato"], "superato")
        self.assertEqual(mq.valuta(fase, sonda, {"contesto_durevole": testo})["stato"], "da_revisionare")
        self.assertEqual(mq.valuta(fase, "Premessa\n" + sonda, memoria(testo))["stato"], "non_conclusivo")

    def test_formato_invalido_non_supera(self):
        for testo in ("", "null", "[]", "{}", '{"valore": 3, "certezza": "confermato", "evidenza": null}'):
            with self.subTest(testo=testo):
                self.assertEqual(mq.valuta(mq.CASI["ipotesi"][0], testo, {})["stato"], "non_conclusivo")

    def test_vecchia_preferenza_e_eccezione_non_passano_inosservate(self):
        for caso, parola in (("correzione", "sintetiche"), ("temporanea", "tre parole")):
            testo = "Preferisce risposte dettagliate. In precedenza: " + parola
            self.assertEqual(
                mq.valuta(mq.CASI[caso][1], risposta("dettagliate", "confermato", testo), memoria(testo))["stato"],
                "da_revisionare",
            )

    def test_piano_abbandonato_non_puo_tornare_attuale(self):
        fase = mq.CASI["abbandono_piano"][1]
        prima = memoria("Il progetto attuale e' ORIONE-42.")
        dopo = memoria("Il progetto attuale e' VEGA-19.")
        self.assertEqual(
            mq.valuta(fase, risposta("ORIONE-42", "confermato", "ORIONE-42"), dopo, prima)["stato"], "fallito"
        )
        self.assertEqual(
            mq.valuta(fase, risposta("VEGA-19", "confermato", "Il progetto attuale e' VEGA-19."), dopo, prima)["stato"],
            "superato",
        )

    def test_avvio_precedente_da_profilo_memoria_e_fonti_non_ammesse(self):
        for stato in (
            {"profilo": {"current_focus": "ORIONE-42 (iniziato)"}},
            memoria("Ha deciso di realizzare ORIONE-42; ha confermato l'avvio dei lavori."),
            memoria("Ha confermato di aver iniziato a lavorare a ORIONE-42."),
        ):
            with self.subTest(stato=stato):
                self.assertTrue(mq.avvio_confermato(stato, "ORIONE-42"))
        for stato in (
            {"contesto": {"summary": "Ha iniziato a lavorare a ORIONE-42."}},
            {"contesto_durevole": "ORIONE-42 (iniziato)"},
            {"memorie": {"memories": [{"source": "Ha iniziato a lavorare a ORIONE-42."}]}},
            memoria("Ha iniziato a lavorare a ORIONE-420."),
            {"profilo": {"current_focus": "ORIONE-42 (iniziato)"}, **memoria("Non ha iniziato ORIONE-42.")},
        ):
            with self.subTest(stato=stato):
                self.assertFalse(mq.avvio_confermato(stato, "ORIONE-42"))

    def test_interrupt_senza_checkpoint_resta_interrotto(self):
        with (
            patch.object(mq.subprocess, "run", side_effect=KeyboardInterrupt),
            self.assertRaises(mq.CasoInterrotto) as e,
        ):
            mq.esegui_caso("correzione", 1, 180)
        self.assertEqual(mq.aggrega([e.exception.risultato])["errore"], 1)

    @unittest.skipUnless(os.name == "posix", "il segnale al gruppo di processi e' una verifica POSIX")
    def test_ctrl_c_reale_salva_json_markdown_e_ferma_la_cli(self):
        with tempfile.TemporaryDirectory(prefix="ares-eval-sigint-") as root:
            root = Path(root)
            rapporto = root / "rapporto.json"
            pronto = root / "pronto.txt"
            figlio = root / "figlio.py"
            figlio.write_text(
                "import json, signal, sys\nfrom pathlib import Path\n"
                "p = Path(sys.argv[1])\n"
                "p.write_text(json.dumps({'fasi': [{'nome': 'iniziale', 'valutazione': {'stato': 'superato'}}]}))\n"
                "pronto = Path(sys.argv[2])\n"
                "pronto.with_suffix('.tmp').write_text(str(p.parent))\n"
                "pronto.with_suffix('.tmp').replace(pronto)\n"
                "signal.pause()\n",
                encoding="utf-8",
            )
            avvio = (
                "import sys\nfrom evals import memory_quality as mq\n"
                "originale = mq.subprocess.run\n"
                "mq.metadati = lambda: {}\n"
                "# Copia gli argomenti del figlio prima di sostituire quelli della CLI.\n"
                "vecchi = list(sys.argv)\n"
                "def lancia(comando, **kwargs):\n"
                "    return originale([sys.executable, vecchi[1], comando[-1], vecchi[2]], **kwargs)\n"
                "mq.subprocess.run = lancia\n"
                "sys.argv = ['eval', '--casi', 'correzione', 'recupero', '--ripetizioni', '1', '--report', vecchi[3]]\n"
                "raise SystemExit(mq.main())\n"
            )
            proc = subprocess.Popen(
                [sys.executable, "-c", avvio, str(figlio), str(pronto), str(rapporto)],
                cwd=mq.RADICE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                start_new_session=True,
            )
            try:
                limite = time.monotonic() + 10
                while not pronto.exists() and proc.poll() is None and time.monotonic() < limite:
                    time.sleep(0.02)
                self.assertTrue(pronto.exists(), "il processo figlio non ha prodotto il checkpoint")
                os.killpg(proc.pid, signal.SIGINT)
                out, err = proc.communicate(timeout=10)
                self.assertEqual(proc.returncode, 1, out + err)
                dati = json.loads(rapporto.read_text(encoding="utf-8"))
                self.assertEqual(dati["stato"], "interrotto")
                self.assertEqual(len(dati["risultati"]), 1)
                self.assertEqual(dati["riepilogo"]["superato"], 1)
                self.assertEqual(dati["riepilogo"]["errore"], 1)
                self.assertIn("interrotto", rapporto.with_suffix(".md").read_text(encoding="utf-8"))
                self.assertFalse(Path(pronto.read_text()).exists())
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate(timeout=10)

    def test_abbandono_senza_memoria_precedente_non_e_un_successo(self):
        fase = mq.CASI["abbandono_ipotesi"][1]
        sonda = risposta("VEGA-19", "confermato", "VEGA-19")
        for prima in ({}, {"contesto": {"summary": "ORIONE-42"}}):
            self.assertEqual(mq.valuta(fase, sonda, memoria("VEGA-19"), prima)["stato"], "non_conclusivo")

    def test_ricordo_storico_richiede_lettura_senza_fallire_automaticamente(self):
        fase = mq.CASI["abbandono_piano"][1]
        dopo = memoria("ORIONE-42 e' abbandonato. Il progetto attuale e' VEGA-19.")
        self.assertEqual(
            mq.valuta(fase, risposta("VEGA-19", "confermato", "VEGA-19"), dopo, memoria("ORIONE-42"))["stato"],
            "da_revisionare",
        )

    def test_memoria_mancante_non_maschera_una_risposta_sbagliata(self):
        fase = mq.CASI["abbandono_ipotesi"][1]
        self.assertEqual(mq.valuta(fase, risposta("ORIONE-42", "confermato", "ORIONE-42"), {}, {})["stato"], "fallito")

    def test_isolamento_anche_per_ingresso_worker(self):
        ambiente = dict(os.environ)
        cwd = Path.cwd()
        cartelle = []

        def finto_worker(caso, rapporto):
            self.assertNotIn("ares.config", sys.modules)
            self.assertTrue(rapporto.is_absolute())
            cartelle.append(Path.cwd())
            for nome in ("ARES_HOME", "ARES_TMP", "ARES_BACKUP_DIR"):
                self.assertTrue(Path(os.environ[nome]).is_relative_to(Path.cwd()))
            raise RuntimeError("guasto simulato")

        with patch.object(mq, "_worker", side_effect=finto_worker), self.assertRaisesRegex(RuntimeError, "simulato"):
            mq.worker("recupero", Path("finto.json"))
        self.assertEqual(dict(os.environ), ambiente)
        self.assertEqual(Path.cwd(), cwd)
        self.assertFalse(cartelle[0].exists())
        with patch.dict(sys.modules, {"ares.config": object()}), self.assertRaisesRegex(RuntimeError, "processo"):
            mq.worker("recupero", Path("finto.json"))

    def test_processo_senza_rapporto_e_un_errore(self):
        with patch.object(mq.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            risultato = mq.esegui_caso("recupero", 1, 1)
        self.assertEqual(mq.aggrega([risultato])["errore"], 1)

    def test_timeout_conserva_fasi_gia_scritte(self):
        cartelle = []

        def timeout(comando, **kwargs):
            root = Path(comando[-1]).parent
            cartelle.append(root)
            (root / "ares-memory-worker-finto").mkdir()
            (root / "ares-memory-worker-finto" / "stato.db").touch()
            mq.scrivi_json(Path(comando[-1]), {"fasi": [{"nome": "iniziale", "valutazione": {"stato": "superato"}}]})
            raise subprocess.TimeoutExpired(comando, 1)

        with patch.object(mq.subprocess, "run", side_effect=timeout):
            risultato = mq.esegui_caso("correzione", 1, 1)
        self.assertEqual(mq.aggrega([risultato])["superato"], 1)
        self.assertEqual(mq.aggrega([risultato])["errore"], 1)
        self.assertFalse(cartelle[0].exists())

    def test_rapporto_distingue_tutti_gli_esiti(self):
        risultati = [
            {"caso": "esempio", "ripetizione": 1, "fasi": [{"nome": s, "valutazione": {"stato": s}} for s in mq.STATI]}
        ]
        self.assertEqual(mq.aggrega(risultati), dict.fromkeys(mq.STATI, 1))
        with tempfile.TemporaryDirectory() as root:
            percorso = Path(root) / "rapporto.json"
            rapporto = {"stato": "completato", "risultati": risultati}
            mq.scrivi_json(percorso, rapporto)
            self.assertEqual(json.loads(percorso.read_text(encoding="utf-8")), rapporto)
            self.assertIn("non_conclusivo", mq.markdown(rapporto))


if __name__ == "__main__":
    unittest.main(verbosity=2)
