# Doppioni ed eliminazione delle lacune (zeno_engine/lacune.py), su dati
# inventati in una cartella temporanea.
#   ../.venv_zeno/bin/python -m unittest tests.test_lacune -v

import json
import os
import tempfile
import unittest

from zeno_engine import lacune


def _tensione(a, b, data='2026-11-01', nota=''):
    return (f'Tensione rilevata sul tema "Etica": il {data} scrivevi «{a}», '
            f'il 2026-11-02 «{b}»' + (f' — {nota}' if nota else ''))


class Chiave(unittest.TestCase):
    def test_tensione_con_frasi_invertite_e_altra_data_e_un_doppione(self):
        self.assertEqual(lacune.chiave(_tensione('Il mare è blu', 'Il mare è grigio')),
                         lacune.chiave(_tensione('Il mare è grigio.', 'il mare è blu',
                                                 data='2026-11-05', nota='altra nota')))

    def test_tensioni_diverse_restano_diverse(self):
        self.assertNotEqual(lacune.chiave(_tensione('Il mare è blu', 'Il mare è grigio')),
                            lacune.chiave(_tensione('Il mare è blu', 'Il cielo è grigio')))

    def test_stesso_tema_scritto_in_due_modi(self):
        a = 'Il tema "Fisica" non ha ancora nessuna traccia del tuo pensiero diretto.'
        b = 'Il tema "Fisica" è quasi assente dal tuo pensiero diretto (solo 2 tuoi documenti).'
        self.assertEqual(lacune.chiave(a), lacune.chiave(b))

    def test_testo_libero_ignora_punteggiatura_e_accenti(self):
        self.assertEqual(lacune.chiave('Perché  scrivo?'), lacune.chiave('perche scrivo'))


class FileLacune(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.voci = [
            {'testo': _tensione('A uno', 'B due'), 'data_prima_rilevazione': '2026-11-03'},
            {'testo': _tensione('B due', 'A uno', data='2026-11-09'),
             'data_prima_rilevazione': '2026-11-01'},
            {'testo': 'Il tema "Arte" è quasi assente dal tuo pensiero diretto.',
             'data_prima_rilevazione': '2026-11-02'},
        ]
        self._scrivi(self.voci)

    def _scrivi(self, voci):
        with open(os.path.join(self.dir, lacune.APERTE), 'w', encoding='utf-8') as f:
            json.dump(voci, f)

    def _leggi(self, nome):
        return json.load(open(os.path.join(self.dir, nome), encoding='utf-8'))

    def test_unisci_tiene_la_voce_piu_vecchia(self):
        self.assertEqual(lacune.conta_doppioni(self.dir), 1)
        self.assertEqual(lacune.unisci_doppioni(self.dir), 1)
        rimaste = self._leggi(lacune.APERTE)
        self.assertEqual(len(rimaste), 2)
        self.assertIn('2026-11-01', [l['data_prima_rilevazione'] for l in rimaste])
        self.assertEqual(lacune.conta_doppioni(self.dir), 0)

    def test_elimina_toglie_le_copie_e_la_segna_rifiutata(self):
        n = lacune.elimina(self.dir, self.voci[0]['testo'])
        self.assertEqual(n, 2)
        self.assertEqual(len(self._leggi(lacune.APERTE)), 1)
        self.assertIn(lacune.chiave(self.voci[1]['testo']), lacune.chiavi_rifiutate(self.dir))
        lacune.elimina(self.dir, self.voci[1]['testo'])   # idempotente
        self.assertEqual(len(self._leggi(lacune.RIFIUTATE)), 1)


if __name__ == '__main__':
    unittest.main()
