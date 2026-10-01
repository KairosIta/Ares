"""Verifica offline che l'eval della latenza misuri cio' che dice, su metriche scritte a mano."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals import latenza as lt


def metrica(secondi: float, entrata: int, uscita: int) -> SimpleNamespace:
    return SimpleNamespace(
        provider_metrics={"total_duration": int(secondi * 1e9)}, input_tokens=entrata, output_tokens=uscita
    )


def risposta(*, modello: list, apprendimento: list, finestre: list[int], testo: str = "ok") -> SimpleNamespace:
    messaggi = [SimpleNamespace(role="user", metrics=None)]
    messaggi += [SimpleNamespace(role="assistant", metrics=SimpleNamespace(input_tokens=f)) for f in finestre]
    return SimpleNamespace(
        metrics=SimpleNamespace(details={"model": modello, "learning_model": apprendimento}),
        messages=messaggi,
        content=testo,
    )


class LatenzaEvalTest(unittest.TestCase):
    def test_misura_somma_le_chiamate_e_legge_l_ultima_finestra(self):
        r = risposta(
            modello=[metrica(10.0, 8000, 300), metrica(5.5, 8500, 50)],
            apprendimento=[metrica(20.0, 5000, 400), metrica(6.0, 5100, 100), metrica(4.0, 5200, 50)],
            finestre=[8000, 8500],
            testo="Ciao\nMarco",
        )
        riga = lt.misura_turno("presentazione", r, strumenti=1, appreso=8, secondi_turno=52.26)
        self.assertEqual(riga["risposta_s"], 15.5)
        self.assertEqual(riga["estrazione_s"], 30.0)
        self.assertEqual(riga["turno_s"], 52.3)
        self.assertEqual(riga["finestra_tok"], 8500)
        self.assertEqual(riga["risposta_tok_out"], 350)
        self.assertEqual((riga["estrazione_tok_in"], riga["estrazione_tok_out"]), (15300, 550))
        self.assertEqual((riga["strumenti"], riga["appreso"]), (1, 8))
        self.assertEqual(riga["testo"], "Ciao Marco")

    def test_senza_risposta_restano_solo_i_secondi_del_turno(self):
        riga = lt.misura_turno("vincolo", None, strumenti=0, appreso=0, secondi_turno=3.0)
        self.assertEqual((riga["risposta_s"], riga["estrazione_s"], riga["turno_s"]), (0.0, 0.0, 3.0))
        self.assertEqual((riga["finestra_tok"], riga["testo"]), (0, ""))

    def test_metriche_senza_durata_del_fornitore_non_rompono(self):
        r = risposta(
            modello=[SimpleNamespace(provider_metrics=None, input_tokens=None, output_tokens=7)],
            apprendimento=[],
            finestre=[],
        )
        riga = lt.misura_turno("preferenze", r, strumenti=0, appreso=0, secondi_turno=1.0)
        self.assertEqual((riga["risposta_s"], riga["risposta_tok_out"], riga["finestra_tok"]), (0.0, 7, 0))

    def test_aggrega_separa_i_turni_con_strumenti(self):
        turni = [
            {
                "turno": "a",
                "risposta_s": 40.0,
                "estrazione_s": 20.0,
                "turno_s": 60.0,
                "finestra_tok": 8000,
                "strumenti": 0,
            },
            {
                "turno": "b",
                "risposta_s": 20.0,
                "estrazione_s": 30.0,
                "turno_s": 50.0,
                "finestra_tok": 9000,
                "strumenti": 0,
            },
            {
                "turno": "c",
                "risposta_s": 240.0,
                "estrazione_s": 40.0,
                "turno_s": 280.0,
                "finestra_tok": 14000,
                "strumenti": 5,
            },
        ]
        r = lt.aggrega(turni)
        self.assertEqual((r["turni"], r["risposta_s"], r["estrazione_s"], r["turno_s"]), (3, 100.0, 30.0, 130.0))
        self.assertEqual(r["senza_strumenti"], {"turni": 2, "risposta_s": 30.0})
        self.assertEqual(r["con_strumenti"], {"turni": 1, "risposta_s": 240.0})
        self.assertEqual(r["finestra_finale_tok"], 14000)
        vuoto = lt.aggrega([])
        self.assertEqual((vuoto["turni"], vuoto["risposta_s"], vuoto["finestra_finale_tok"]), (0, None, 0))

    def test_markdown_porta_tabella_medie_ed_errore(self):
        turni = [
            {
                "turno": "a",
                "risposta_s": 40.0,
                "estrazione_s": 20.0,
                "turno_s": 60.0,
                "finestra_tok": 8000,
                "risposta_tok_out": 300,
                "strumenti": 0,
                "appreso": 8,
            },
        ]
        rapporto = {
            "metadati": {
                "modello_conversazione": "m",
                "modello_estrazione": "e",
                "opzioni_conversazione": {"num_ctx": 65536},
            },
            "turni": turni,
            "riepilogo": lt.aggrega(turni),
            "errore": "Timeout dopo 10 secondi.",
        }
        testo = lt.markdown(rapporto)
        self.assertIn("| a | 40.0 s | 20.0 s | 60.0 s | 8000 | 300 | 0 | 8 |", testo)
        self.assertIn("Contesto: 65536", testo)
        self.assertIn("Senza strumenti (1 turni): risposta 40.0 s. Con strumenti (0 turni): risposta —.", testo)
        self.assertIn("Errore: Timeout dopo 10 secondi.", testo)

    def test_importare_l_eval_non_importa_la_configurazione(self):
        # Il worker prepara i percorsi temporanei prima di importare Ares:
        # un import di `ares.config` a livello di modulo lo renderebbe impossibile.
        self.assertNotIn("ares.config", sys.modules)
        self.assertEqual(len(lt.TURNI), 6)
        self.assertEqual([n for n, _ in lt.TURNI][:2], ["presentazione", "preferenze"])


if __name__ == "__main__":
    unittest.main()
