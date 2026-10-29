"""Listening reconstruction uses exact hit deltas, preserving overlaps, spectral rides and tails."""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from slate import jsonio
from slate.errors import MissingPrerequisite
from slate.sound import audio, listen


@pytest.fixture
def film(tmp_path):
    return SimpleNamespace(dir=tmp_path,sr=16000,n=32000,path=lambda ref:tmp_path/ref)


def write_delta(film,name,values,start=0):
    path=audio.write_wav(film.dir/f'sound/mix/ride_deltas/{name}.wav',values,film.sr,32)
    return {'hit':name,'delta':{'file':str(path.relative_to(film.dir)),'sha256':jsonio.sha256(path),
                              'start_sample':start}}


def test_exact_delta_does_not_change_overlapping_unridden_sound_or_drop_tail(film):
    t=np.arange(1200)/film.sr
    untouched=np.repeat((.1*np.sin(2*np.pi*200*t))[:,None],2,axis=1)
    delta=np.repeat((.03*np.sin(2*np.pi*3100*t))[:,None],2,axis=1)
    # The other hit overlaps the entire excerpt; undoing a top ride must not scale its body.
    now=untouched.copy()
    ride=write_delta(film,'one',delta,start=1000)
    before=listen._before_excerpt(now,1000,[listen._ride_delta(film,ride)],film.sr)
    assert np.allclose(before-now,delta,atol=2e-9)
    assert np.array_equal(now,untouched)
    assert np.max(abs((before-now)[800:]))>.02  # the original dry hit ended before its own reverb tail


def test_delta_range_is_clipped_to_excerpt_and_multiple_hits_sum(film):
    a=write_delta(film,'a',np.full((200,2),.1),start=50)
    b=write_delta(film,'b',np.full((80,2),.2),start=230)
    before=listen._before_excerpt(np.zeros((200,2)),150,
                                  [listen._ride_delta(film,a),listen._ride_delta(film,b)],film.sr)
    expected=np.zeros((200,2));expected[:100]+=.1;expected[80:160]+=.2
    assert np.allclose(before,expected,atol=1e-8)


@pytest.mark.parametrize('damage',['missing_record','missing_file','changed','outside','start','rate','channels','bounds'])
def test_missing_or_changed_delta_refuses(film,damage,tmp_path):
    ride=write_delta(film,'one',np.ones((100,2))*.01)
    path=film.path(ride['delta']['file'])
    if damage=='missing_record': ride.pop('delta')
    elif damage=='missing_file': path.unlink()
    elif damage=='changed': path.write_bytes(path.read_bytes()+b'changed')
    elif damage=='outside': ride['delta']['file']='../elsewhere.wav'
    elif damage=='start': ride['delta']['start_sample']=True
    elif damage=='rate':
        audio.write_wav(path,np.ones((100,2)),8000,32);ride['delta']['sha256']=jsonio.sha256(path)
    elif damage=='channels':
        audio.write_wav(path,np.ones((100,1)),film.sr,32);ride['delta']['sha256']=jsonio.sha256(path)
    elif damage=='bounds': ride['delta']['start_sample']=film.n-50
    with pytest.raises(MissingPrerequisite) as error:
        listen._ride_delta(film,ride)
    assert 'slate mix' in str(error.value.hint)


def test_nonfinite_delta_refuses_during_excerpt_read(film):
    ride=write_delta(film,'bad',np.full((100,2),np.nan))
    with pytest.raises(MissingPrerequisite,match='invalid audio'):
        listen._before_excerpt(np.zeros((100,2)),0,[listen._ride_delta(film,ride)],film.sr)
