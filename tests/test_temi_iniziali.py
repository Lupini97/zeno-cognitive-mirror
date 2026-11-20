# Temi con cui parte un archivio nuovo (zeno_engine/db.py): generici di
# serie, sostituibili da temi.iniziali in zeno_config.yaml. Dati inventati,
# database in una cartella temporanea; la config vera non viene letta.
#   ../.venv_zeno/bin/python -m unittest tests.test_temi_iniziali -v

import os
import tempfile
import unittest
from unittest import mock

from zeno_engine import db


def _temi(percorso):
    con = db.connect(percorso)
    try:
        return dict(con.execute('SELECT name, descrizione FROM topics ORDER BY id'))
    finally:
        con.close()


class TemiIniziali(unittest.TestCase):
    def setUp(self):
        self.cartella = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.cartella.name, 'zeno.db')

    def tearDown(self):
        self.cartella.cleanup()

    def test_senza_config_parte_con_i_temi_generici(self):
        with mock.patch.object(db, '_sezione_temi', return_value={}):
            temi = _temi(self.db)
        self.assertEqual(list(temi), list(db.TEMI_GENERICI))
        self.assertTrue(all(temi.values()), 'ogni tema generico ha la sua descrizione')

    def test_temi_iniziali_dalla_config(self):
        sezione = {'iniziali': [{'nome': 'Fotografia', 'descrizione': 'tecnica e autori'},
                                'Viaggi', {'nome': '  '}]}
        with mock.patch.object(db, '_sezione_temi', return_value=sezione):
            temi = _temi(self.db)
        self.assertEqual(temi, {'Fotografia': 'tecnica e autori', 'Viaggi': None})

    def test_i_temi_si_inseriscono_solo_nel_db_nuovo(self):
        with mock.patch.object(db, '_sezione_temi', return_value={}):
            con = db.connect(self.db)
            con.execute("UPDATE topics SET name = 'Rinominato' WHERE name = 'Storia'")
            con.commit()
            con.close()
            temi = _temi(self.db)
        self.assertIn('Rinominato', temi)
        self.assertNotIn('Storia', temi)

    def test_descrizioni_extra_e_iniziali(self):
        sezione = {'iniziali': [{'nome': 'Fotografia', 'descrizione': 'tecnica e autori'}, 'Viaggi'],
                   'descrizioni': {'Cucina': 'ricette e ingredienti'}}
        with mock.patch.object(db, '_sezione_temi', return_value=sezione):
            self.assertEqual(db.descrizioni_temi(),
                             {'Fotografia': 'tecnica e autori', 'Cucina': 'ricette e ingredienti'})


if __name__ == '__main__':
    unittest.main()
