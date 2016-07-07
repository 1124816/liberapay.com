from __future__ import absolute_import, division, print_function, unicode_literals

from mock import patch

from mangopaysdk.entities.payin import PayIn
from mangopaysdk.entities.payout import PayOut
from mangopaysdk.entities.refund import Refund
from mangopaysdk.types.refundreason import RefundReason

from liberapay.billing.exchanges import Money, record_exchange
from liberapay.models.exchange_route import ExchangeRoute
from liberapay.testing.emails import EmailHarness
from liberapay.testing.mangopay import MangopayHarness


class TestMangopayCallbacks(EmailHarness, MangopayHarness):

    def callback(self, qs, **kw):
        kw.setdefault('raise_immediately', False)
        return self.client.GET('/callbacks/mangopay?'+qs, **kw)

    @patch('mangopaysdk.tools.apipayouts.ApiPayOuts.Get')
    def test_payout_callback(self, Get):
        homer, ba = self.homer, self.homer_route
        for status in ('succeeded', 'failed'):
            status_up = status.upper()
            error = 'FOO' if status == 'failed' else None
            self.make_exchange('mango-cc', 10, 0, homer)
            e_id = record_exchange(self.db, ba, -10, 0, 0, homer, 'pre')
            assert homer.balance == 0
            homer.close(None)
            assert homer.status == 'closed'
            qs = "EventType=PAYOUT_NORMAL_"+status_up+"&RessourceId=123456790"
            payout = PayOut()
            payout.Status = status_up
            payout.ResultCode = '000001' if error else '000000'
            payout.ResultMessage = error
            payout.AuthorId = homer.mangopay_user_id
            payout.Tag = str(e_id)
            Get.return_value = payout
            r = self.callback(qs)
            assert b'csrf_token' not in r.headers.cookie
            assert r.code == 200, r.text
            homer = homer.refetch()
            if status == 'succeeded':
                assert homer.balance == 0
                assert homer.status == 'closed'
            else:
                assert homer.balance == 10
                assert homer.status == 'active'
                emails = self.get_emails()
                assert len(emails) == 1
                assert emails[0]['to'][0] == 'homer <%s>' % homer.email
                assert 'fail' in emails[0]['subject']
            homer.update_status('active')  # reset for next loop run

    @patch('mangopaysdk.tools.apipayouts.ApiPayOuts.Get')
    @patch('mangopaysdk.tools.apirefunds.ApiRefunds.Get')
    def test_payout_refund_callback(self, R_Get, PO_Get):
        homer, ba = self.homer, self.homer_route
        for status in ('failed', 'succeeded'):
            # Create the payout
            self.make_exchange('mango-cc', 10, 0, homer)
            e_id = record_exchange(self.db, ba, -9, 1, 0, homer, 'pre')
            assert homer.balance == 0
            homer.close(None)
            assert homer.status == 'closed'
            payout = PayOut()
            payout.Status = 'SUCCEEDED'
            payout.ResultCode = '000000'
            payout.AuthorId = homer.mangopay_user_id
            payout.Tag = str(e_id)
            PO_Get.return_value = payout
            # Create the refund
            status_up = status.upper()
            error = 'FOO' if status == 'failed' else None
            refund = Refund()
            refund.DebitedFunds = Money(900, 'EUR')
            refund.Fees = Money(-100, 'EUR')
            refund.Status = status_up
            refund.ResultCode = '000001' if error else '000000'
            refund.ResultMessage = error
            reason = refund.RefundReason = RefundReason()
            reason.RefundReasonMessage = 'BECAUSE 42'
            refund.AuthorId = homer.mangopay_user_id
            R_Get.return_value = refund
            # Call back
            qs = "EventType=PAYOUT_REFUND_"+status_up+"&RessourceId=123456790"
            r = self.callback(qs)
            assert r.code == 200, r.text
            homer = homer.refetch()
            if status == 'failed':
                assert homer.balance == 0
                assert homer.status == 'closed'
            else:
                assert homer.balance == 10
                assert homer.status == 'active'
                emails = self.get_emails()
                assert len(emails) == 1
                assert emails[0]['to'][0] == 'homer <%s>' % homer.email
                assert 'fail' in emails[0]['subject']
                assert 'BECAUSE 42' in emails[0]['text']
            homer.update_status('active')  # reset for next loop run

    @patch('mangopaysdk.tools.apipayins.ApiPayIns.Get')
    def test_payin_bank_wire_callback(self, Get):
        homer = self.homer
        route = ExchangeRoute.insert(homer, 'mango-bw', 'x')
        for status in ('failed', 'succeeded'):
            status_up = status.upper()
            error = 'FOO' if status == 'failed' else None
            e_id = record_exchange(self.db, route, 11, 0, 0, homer, 'pre')
            assert homer.balance == 0
            homer.close(None)
            assert homer.status == 'closed'
            qs = "EventType=PAYIN_NORMAL_"+status_up+"&RessourceId=123456790"
            payin = PayIn()
            payin.Status = status_up
            payin.ResultCode = '000001' if error else '000000'
            payin.ResultMessage = error
            payin.AuthorId = homer.mangopay_user_id
            payin.PaymentType = 'BANK_WIRE'
            payin.Tag = str(e_id)
            Get.return_value = payin
            r = self.callback(qs)
            assert r.code == 200, r.text
            homer = homer.refetch()
            if status == 'succeeded':
                assert homer.balance == 11
                assert homer.status == 'active'
            else:
                assert homer.balance == 0
                assert homer.status == 'closed'
            emails = self.get_emails()
            assert len(emails) == 1
            assert emails[0]['to'][0] == 'homer <%s>' % homer.email
            assert status[:4] in emails[0]['subject']
            homer.update_status('active')  # reset for next loop run
