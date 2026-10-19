"""Read-only stream recovery tests: no SDK, network, credentials or real clock."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ig_client import IGError
from runtime import Monitor
from storage import Store


class FakeStream:
    def __init__(self,status_callback):
        self.status_callback=status_callback;self.disconnected=0;self.unsubscribed=[]
    def disconnect(self):
        self.disconnected+=1;self.status_callback('DISCONNECTED')
    def unsubscribe(self,sub):self.unsubscribed.append(sub)


class FakeClient:
    account_id='A'
    def __init__(self):
        self.failures=[];self.attempts=0;self.streams=[];self.price_calls=[];self.callbacks=[]
        self.login=Mock(return_value=({'accountId':'A','currency':'HKD','balance':{}},[]))
    def make_stream(self,callback,status_callback):
        self.attempts+=1;self.callbacks.append((callback,status_callback))
        if self.failures:
            failure=self.failures.pop(0)
            if failure:raise failure
        stream=FakeStream(status_callback);self.streams.append(stream)
        def subscribe(kind,items,fields,**kwargs):
            self.price_calls.append((kind,items,fields,kwargs));return object()
        status_callback('CONNECTING')
        return stream,subscribe,[]


class StreamRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.clock=1000.0
        self.store_patch=patch('runtime.Store',lambda:Store(':memory:'));self.store_patch.start()
        self.clock_patch=patch('runtime.time.monotonic',lambda:self.clock);self.clock_patch.start()
        self.time_patch=patch('runtime.time.time',lambda:self.clock);self.time_patch.start()
        self.m=Monitor();self.m.status=Mock();self.client=FakeClient();self.m.client=self.client
        self.m.state['positions']=[{'market':{'epic':'ONE'}},{'market':{'epic':'ONE'}},{'market':{'epic':'TWO'}}]
    def tearDown(self):
        self.m.store.db.close();self.time_patch.stop();self.clock_patch.stop();self.store_patch.stop()
    def advance(self,seconds):self.clock+=seconds;self.m.recover_stream()
    def connected(self):
        self.m.start_stream();self.client.callbacks[-1][1]('CONNECTED:WS-STREAMING')

    def test_initial_failure_retries_same_client_and_subscribes_once(self):
        self.client.failures=[IGError('ig.network.or.tls.error')]
        self.m.start_stream();self.assertIsNone(self.m.stream)
        # A factory can leave an SDK object alive even when it never returns it.
        self.client.callbacks[-1][1]('CONNECTED:WS-STREAMING')
        self.assertEqual(self.m.state['stream'],'ig.network.or.tls.error')
        self.advance(29);self.assertEqual(self.client.attempts,1)
        self.advance(1);self.assertEqual(self.client.attempts,2)
        self.assertIs(self.m.client,self.client);self.client.login.assert_not_called()
        self.assertEqual(self.client.price_calls[0][1],['PRICE:A:ONE','PRICE:A:TWO'])
        self.m.update_subscriptions();self.m.recover_stream()
        self.assertEqual(len(self.client.price_calls),1)

    def test_rest_failure_after_login_still_bootstraps_stream_after_grace(self):
        self.m.client=None;self.m.creds={'environment':'live','history_since':'2026-10-01'}
        self.m.reconcile=Mock(side_effect=IGError('ig.network.or.tls.error'))
        with patch('runtime.IGClient',return_value=self.client):
            with self.assertRaises(IGError):self.m.connect()
        self.assertIs(self.m.client,self.client);self.client.login.assert_called_once()
        self.advance(29);self.assertEqual(self.client.attempts,0)
        self.advance(1);self.assertEqual(self.client.attempts,1)
        self.client.login.assert_called_once()

    def test_terminal_disconnect_replaces_stream_after_grace(self):
        self.connected();old=self.m.stream;old_callback=self.client.callbacks[-1][1]
        old_callback('DISCONNECTED');deadline=self.m.stream_retry_at
        self.advance(20);old_callback('DISCONNECTED')
        self.assertEqual(self.m.stream_retry_at,deadline)
        self.advance(10);self.assertEqual(self.client.attempts,2)
        self.assertEqual(old.disconnected,1)
        self.assertIsNot(self.m.stream,old);self.assertEqual(len(self.client.price_calls),2)

    def test_sdk_retries_stall_and_active_connection_are_never_replaced(self):
        self.connected()
        for status in ('CONNECTING','STALLED','DISCONNECTED:WILL-RETRY','DISCONNECTED:TRYING-RECOVERY',
                       'CONNECTED:HTTP-STREAMING','CONNECTED:WS-STREAMING'):
            self.client.callbacks[-1][1](status);self.advance(1000)
        self.assertEqual(self.client.attempts,1);self.assertEqual(len(self.client.price_calls),1)

    def test_sdk_progress_cancels_terminal_timer_and_next_terminal_gets_grace(self):
        self.connected();status=self.client.callbacks[-1][1]
        status('DISCONNECTED');self.advance(10);status('DISCONNECTED:WILL-RETRY')
        self.advance(1000);self.assertEqual(self.client.attempts,1)
        status('DISCONNECTED');self.advance(29);self.assertEqual(self.client.attempts,1)
        self.advance(31);self.assertEqual(self.client.attempts,2)

    def test_sdk_resuming_after_watchdog_due_does_not_replace_or_clear_subscriptions(self):
        for resumed in ('CONNECTED:WS-STREAMING','CONNECTING'):
            with self.subTest(status=resumed):
                self.connected();stream=self.m.stream;subscribe=self.m.subscribe;price_sub=self.m.price_sub
                subscribed=list(self.m.subscribed);generation=self.m.generation
                status=self.client.callbacks[-1][1];status('DISCONNECTED');self.clock+=30
                attempts=self.client.attempts;start_stream=self.m.start_stream
                def resume_then_start(*args,**kwargs):
                    # Exercise the gap after recover_stream releases its due
                    # check lock, before start_stream acquires the same lock.
                    status(resumed)
                    return start_stream(*args,**kwargs)
                with patch.object(self.m,'start_stream',side_effect=resume_then_start):self.m.recover_stream()
                self.assertEqual(self.client.attempts,attempts);self.assertEqual(stream.disconnected,0)
                self.assertEqual(self.m.generation,generation);self.assertIs(self.m.stream,stream)
                self.assertIs(self.m.subscribe,subscribe);self.assertIs(self.m.price_sub,price_sub)
                self.assertEqual(self.m.subscribed,subscribed);self.assertEqual(self.m.state['stream'],resumed)

    def test_recovery_from_obsolete_generation_does_not_replace_current_stream(self):
        self.connected();generation=self.m.generation
        self.m.start_stream();stream=self.m.stream;attempts=self.client.attempts
        self.m.start_stream(recovery_generation=generation)
        self.assertIs(self.m.stream,stream);self.assertEqual(self.client.attempts,attempts)
        self.assertEqual(stream.disconnected,0)

    def test_watchdog_respects_timer_renewed_before_atomic_start(self):
        self.connected();stream=self.m.stream;status=self.client.callbacks[-1][1]
        status('DISCONNECTED');self.clock+=30;start_stream=self.m.start_stream
        def renewed_then_start(*args,**kwargs):
            status('DISCONNECTED:WILL-RETRY');status('DISCONNECTED')
            return start_stream(*args,**kwargs)
        with patch.object(self.m,'start_stream',side_effect=renewed_then_start):self.m.recover_stream()
        self.assertIs(self.m.stream,stream);self.assertEqual(self.client.attempts,1)
        self.assertGreaterEqual(self.m.stream_retry_at-self.clock,30)
        self.advance(self.m.stream_retry_at-self.clock)
        self.assertEqual(self.client.attempts,2)

    def test_error_and_rejected_subscription_are_recoverable(self):
        for failure in ('ERROR_40','SUBSCRIPTION_ERROR_1'):
            self.connected();self.client.callbacks[-1][1](failure)
            before=self.client.attempts
            if failure.startswith('SUBSCRIPTION'):
                self.client.callbacks[-1][1]('CONNECTED:WS-STREAMING')
                self.assertEqual(self.m.state['stream'],failure)
            self.advance(29);self.assertEqual(self.client.attempts,before)
            self.advance(1);self.assertEqual(self.client.attempts,before+1)

    def test_repeated_failures_back_off_and_cap_at_five_minutes(self):
        self.client.failures=[OSError('synthetic private error') for _ in range(9)]
        self.m.start_stream()
        for delay in (30,60,120,240,300,300,300,300):
            self.assertEqual(self.m.stream_retry_at-self.clock,delay)
            before=self.client.attempts;self.advance(delay-1);self.assertEqual(self.client.attempts,before)
            self.advance(1);self.assertEqual(self.client.attempts,before+1)
            self.assertEqual(self.m.state['stream'],'ERROR_LOCAL')
        self.assertNotIn('synthetic',str(self.m.state))

    def test_old_status_price_account_and_trade_callbacks_are_ignored(self):
        self.connected();old_data,old_status=self.client.callbacks[-1]
        old_status('DISCONNECTED');self.advance(30)
        self.client.callbacks[-1][1]('CONNECTED:WS-STREAMING')
        self.m.state['account']={'equity':200};self.m.scope='live:A'
        old_status('ERROR_9');old_data('price','PRICE:A:ONE',{'BIDPRICE1':'1'})
        old_data('account','ACCOUNT:A',{'EQUITY':'1'})
        old_data('trade','TRADE:A',{'CONFIRMS':'{"dealId":"OLD"}'})
        self.assertEqual(self.m.state['stream'],'CONNECTED:WS-STREAMING')
        self.assertFalse(self.m.quotes);self.assertEqual(self.m.state['account']['equity'],200)
        self.assertEqual(self.m.store.count('live:A'),0)

    def test_subscription_exception_does_not_mark_failed_set_as_subscribed(self):
        self.connected();self.m.state['positions'].append({'market':{'epic':'THREE'}})
        self.m.subscribe=Mock(side_effect=OSError('private synthetic details'))
        self.m.update_subscriptions()
        self.assertEqual(self.m.state['stream'],'SUBSCRIPTION_ERROR_LOCAL')
        self.assertIsNone(self.m.price_sub);self.assertEqual(self.m.subscribed,[])
        self.m.update_subscriptions();self.m.subscribe.assert_called_once()
        self.advance(30)
        self.assertEqual(self.m.subscribed,['ONE','TWO','THREE'])

    def test_programming_exception_is_scheduled_but_not_silently_swallowed(self):
        self.client.failures=[TypeError('synthetic bug')]
        with self.assertRaises(TypeError):self.m.start_stream()
        self.assertEqual(self.m.state['stream'],'ERROR_LOCAL')
        self.advance(30);self.assertEqual(self.client.attempts,2)

    def test_reset_invalidates_callbacks_and_clears_retry_and_subscriptions(self):
        self.connected();old_status=self.client.callbacks[-1][1]
        old_status('DISCONNECTED');self.assertTrue(self.m.stream_retry_pending)
        self.m.reset({'saved_at':'new'})
        old_status('ERROR_1')
        self.assertIsNone(self.m.client);self.assertIsNone(self.m.stream)
        self.assertIsNone(self.m.subscribe);self.assertIsNone(self.m.price_sub);self.assertEqual(self.m.subscribed,[])
        self.assertFalse(self.m.stream_retry_pending);self.assertEqual(self.m.stream_failures,0)
        self.assertEqual(self.m.stream_retry_at,0);self.assertEqual(self.m.state['stream'],'DISCONNECTED')

    def test_401_uses_existing_session_reset_and_login_backoff(self):
        creds={'saved_at':'one'};iterations=0
        class Stop:
            def wait(_,seconds):
                nonlocal iterations
                iterations+=1;self.clock+=1
                return iterations>2
        self.m.stop=Stop();self.m.reconcile=Mock(side_effect=IGError('error.security.client-token-invalid',401))
        def connect():
            self.m.client=self.client;self.connected();self.m.last_poll=0
        self.m.connect=Mock(side_effect=connect)
        with patch('runtime.load_credentials',return_value=creds):self.m.run()
        self.assertIsNone(self.m.client);self.assertIsNone(self.m.stream);self.assertIsNone(self.m.price_sub)
        self.assertEqual(self.m.subscribed,[]);self.assertFalse(self.m.stream_retry_pending)
        self.assertEqual(self.m.stream_failures,0);self.assertEqual(self.m.retry_at,1062)
        self.m.connect.assert_called_once()

    def test_stream_failure_does_not_stop_rest_collection(self):
        creds={'saved_at':'one'};iterations=0
        class Stop:
            def wait(_,seconds):
                nonlocal iterations
                iterations+=1;self.clock+=1
                return iterations>2
        self.m.stop=Stop();self.m.reconcile=Mock();self.m.funding_sync=Mock()
        self.client.failures=[IGError('ig.network.or.tls.error')]
        def connect():
            self.m.client=self.client;self.m.start_stream();self.m.last_poll=0
        self.m.connect=Mock(side_effect=connect)
        with patch('runtime.load_credentials',return_value=creds):self.m.run()
        self.m.reconcile.assert_called_once();self.assertIs(self.m.client,self.client)
        self.assertEqual(self.client.attempts,1);self.assertTrue(self.m.stream_retry_pending)

    def test_successful_funding_sync_clears_old_error(self):
        self.m.creds={'risk_baseline_at':'2026-10-19T00:00:00+00:00'}
        self.m.state['funding_error']='ig.network.or.tls.error'
        self.client.request=Mock(return_value={'transactions':[],'metadata':{'pageData':{'totalPages':1}}})
        self.m.funding_sync();self.assertNotIn('funding_error',self.m.state)


if __name__=='__main__':unittest.main()
