# Test della sentinella dello stallo del demone MLX (blocco Metal del 17-18/11).
#
# Nessun modello caricato, nessun demone avviato: stream_generate e il
# caricamento dei pesi sono sostituiti da versioni finte, la rete del client
# da funzioni finte.
#
# Lancio (da scripts/):
#   ../.venv_zeno/bin/python -m unittest discover -s tests -t . -v

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zeno_engine import mlx_server, modelli  # noqa: E402
from zeno_engine.mlx_motore import Registro  # noqa: E402


class _Tokenizer:
    def apply_chat_template(self, messaggi, **_):
        return 'prompt'


def _registro_finto():
    registro = Registro()
    voce = SimpleNamespace(tokenizer=_Tokenizer(), modello=None, ultimo_uso=0.0,
                           scadenza=0.0, lucchetto_prefisso=None)
    registro.carica = lambda nome: voce
    return registro


class FermoDa(unittest.TestCase):

    def test_nessuna_generazione(self):
        self.assertEqual(Registro().fermo_da(), 0.0)

    def test_conta_solo_chi_aspetta_il_modello(self):
        registro = Registro()
        with mock.patch('zeno_engine.mlx_motore.time.monotonic', return_value=1000.0):
            registro._attese = {'a': 850.0, 'b': None, 'c': 990.0}
            self.assertEqual(registro.fermo_da(), 150.0)
            registro._attese = {'b': None}
            self.assertEqual(registro.fermo_da(), 0.0)

    def test_soglia(self):
        registro = Registro()
        with mock.patch.object(registro, 'fermo_da', return_value=130.0):
            self.assertEqual(mlx_server._stallo(registro, 120.0), 130.0)
            self.assertEqual(mlx_server._stallo(registro, 180.0), 0.0)


class SorveglianzaInChat(unittest.TestCase):

    def test_attesa_segnata_solo_dentro_il_modello(self):
        registro = _registro_finto()
        visto_dal_modello = []

        def stream_finto(*_, **__):
            for testo in ['Ciao', '', ' mondo']:
                # Dentro il calcolo: la generazione risulta in attesa.
                visto_dal_modello.append(list(registro._attese.values()))
                yield SimpleNamespace(text=testo)

        with mock.patch('mlx_lm.generate.stream_generate', stream_finto):
            flusso = registro.chat('finto', [{'role': 'user', 'content': 'x'}],
                                   opzioni={'pensiero': True})
            pezzi = []
            for pezzo in flusso:
                pezzi.append(pezzo)
                # In mano al chiamante: nessuna attesa del modello.
                self.assertEqual(registro.fermo_da(), 0.0)
                self.assertEqual(list(registro._attese.values()), [None])

        self.assertEqual(''.join(pezzi), 'Ciao mondo')
        self.assertEqual(len(visto_dal_modello), 3)
        self.assertTrue(all(len(v) == 1 and v[0] is not None for v in visto_dal_modello))
        self.assertEqual(registro._attese, {})

    def test_flusso_chiuso_a_meta_non_lascia_attese(self):
        registro = _registro_finto()

        def stream_finto(*_, **__):
            while True:
                yield SimpleNamespace(text='x')

        with mock.patch('mlx_lm.generate.stream_generate', stream_finto):
            flusso = registro.chat('finto', [], opzioni={'pensiero': True})
            next(flusso)
            flusso.close()
        self.assertEqual(registro._attese, {})

    def test_errore_del_modello_non_lascia_attese(self):
        registro = _registro_finto()

        def stream_finto(*_, **__):
            yield SimpleNamespace(text='x')
            raise RuntimeError('Metal')

        with mock.patch('mlx_lm.generate.stream_generate', stream_finto):
            with self.assertRaises(RuntimeError):
                list(registro.chat('finto', [], opzioni={'pensiero': True}))
        self.assertEqual(registro._attese, {})


class RiavvioDalClient(unittest.TestCase):

    def setUp(self):
        self.cliente = modelli.ClienteMLX()

    def _posta(self, raggiungibile, riavviato, esiti):
        chiamate = []

        def una(*_):
            chiamate.append(1)
            esito = esiti[len(chiamate) - 1]
            if isinstance(esito, Exception):
                raise esito
            return esito

        with mock.patch.object(self.cliente, '_posta_una', una), \
                mock.patch.object(modelli, 'raggiungibile', return_value=raggiungibile), \
                mock.patch.object(modelli, 'assicura_demone', return_value=riavviato) as assicura:
            try:
                return self.cliente._posta('/api/chat', {}), len(chiamate), assicura.call_count
            except modelli.ErroreMLX:
                return 'errore', len(chiamate), assicura.call_count

    def test_demone_sparito_riavviato_e_richiesta_ripetuta(self):
        esito = self._posta(False, True, [modelli.ErroreConnessione('caduto'), {'ok': 1}])
        self.assertEqual(esito, ({'ok': 1}, 2, 1))

    def test_demone_vivo_nessun_riavvio(self):
        esito = self._posta(True, True, [modelli.ErroreConnessione('timeout')])
        self.assertEqual(esito, ('errore', 1, 0))

    def test_riavvio_fallito(self):
        esito = self._posta(False, False, [modelli.ErroreConnessione('caduto')])
        self.assertEqual(esito, ('errore', 1, 1))

    def test_una_sola_ripetizione(self):
        esito = self._posta(False, True, [modelli.ErroreConnessione('caduto'),
                                          modelli.ErroreConnessione('ancora')])
        self.assertEqual(esito, ('errore', 2, 1))

    def test_errore_di_risposta_non_ripetuto(self):
        esito = self._posta(False, True, [modelli.ErroreRisposta('HTTP 400')])
        self.assertEqual(esito, ('errore', 1, 0))


if __name__ == '__main__':
    unittest.main()
