from types import SimpleNamespace as NS
from dipbot.gap_recovery import GapRecovery
from test_activity import setup


def make_job(start=100,end=123):
    chain,pool,logs,calls=setup()
    chain.restrict_to_reads=lambda:None
    chain.w3.eth.get_block=lambda n:{'number':n,'hash':b'h'*32}
    return GapRecovery('https://example.invalid',pool,start,end,factory=lambda:chain),logs


def test_bounded_gap_event_recovery_and_deduplication():
    job,logs=make_job(1,123)
    job.run()
    assert job.result['from_block']==92 and job.result['truncated']
    assert job.result['count']==1 and len(job.result['events'])==1
    assert job.result['events'][0]['block']==100
    assert set(job.result['events'][0])=={'block','block_hash','transaction_hash','log_index','data'}


def test_error_not_empty_success_and_no_secret_text():
    job,logs=make_job()
    def broken():raise OSError('https://example.invalid/SECRET')
    job.factory=broken
    job.run()
    assert job.result['error_type']=='OSError' and job.result['count'] is None
    assert 'SECRET' not in str(job.result)


def test_stop_discards_background_result():
    job,logs=make_job()
    job.stop();job.run()
    assert job.result is None


def test_oversized_logs_are_explicit_failure():
    job,logs=make_job()
    logs.extend([logs[0]]*512)
    job.run()
    assert job.result['error_type']=='ValueError' and job.result['count'] is None
