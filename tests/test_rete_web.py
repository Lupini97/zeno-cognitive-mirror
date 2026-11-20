# Interruttore della ricerca web (interfaccia/rete_web.py): spento di serie,
# acceso solo da una scelta esplicita. File in una cartella temporanea.
#   ../.venv_zeno/bin/python -m unittest tests.test_rete_web -v

import os
import tempfile
import unittest
from unittest import mock

from interfaccia import rete_web


class Interruttore(unittest.TestCase):
    def setUp(self):
        self.cartella = tempfile.TemporaryDirectory()
        self.file = os.path.join(self.cartella.name, 'stats', 'ricerca_web.json')
        self.patch = mock.patch.object(rete_web, '_percorso', return_value=self.file)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.cartella.cleanup()

    def test_spenta_di_serie(self):
        self.assertFalse(rete_web.attiva())

    def test_accesa_e_spenta(self):
        rete_web.imposta(True)
        self.assertTrue(rete_web.attiva())
        rete_web.imposta(False)
        self.assertFalse(rete_web.attiva())

    def test_file_rovinato_vale_spenta(self):
        os.makedirs(os.path.dirname(self.file))
        for contenuto in ('non json', '[]', '{"attiva": "si"}'):
            with open(self.file, 'w', encoding='utf-8') as f:
                f.write(contenuto)
            self.assertFalse(rete_web.attiva(), contenuto)

    def test_senza_configurazione_vale_spenta(self):
        with mock.patch.object(rete_web, '_percorso', return_value=None):
            self.assertFalse(rete_web.attiva())


if __name__ == '__main__':
    unittest.main()
