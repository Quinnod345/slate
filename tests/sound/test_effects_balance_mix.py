"""Neutral signals exercise processing and guard decisions without a private film or sample library."""
from __future__ import annotations

import copy
import numpy as np
import pytest
from scipy import signal

from slate.errors import Refused
from slate.sound.effects import balance_mix as bm
from slate.sound.effects import rendering

SR = 16000


def tone(hz=2500, seconds=1.2, level=.15, sr=SR):
    t = np.arange(round(seconds*sr))/sr
    return np.repeat((np.sin(2*np.pi*hz*t)*level)[:,None],2,axis=1)


@pytest.fixture
def raw_cues(monkeypatch):
    """Fixture sounds are array literals; the production renderer still places, sends and groups them."""
    def render(cue, film_dir=None, sample_rate=SR, **kwargs):
        params = cue.get('params') or {}
        y = np.asarray(params.get('samples',cue.get('samples')),dtype=float)
        return y.copy(), -len(y) if cue.get('align') == 'end' else 0
    monkeypatch.setattr(rendering,'render_cue',render)


def cue(key='hit', family='accents', t=.4, *, level=.15, hz=2500, seconds=.16, reverb=None):
    c = {'id':key,'event':key,'family':family,'type':'wood','kind':'tap','stem':'ui',
         't':t,'beat':t,'dur':seconds,'anchor':'onset','samples':tone(hz,seconds,level)}
    if reverb is not None:
        c['reverb'] = reverb
    return c


def mixer(voice=None,config=None,**kwargs):
    return bm.FamilyMixer(tone() if voice is None else voice,sample_rate=SR,config=config,**kwargs)


def no_extra(**kwargs):
    return {'air_guard':False,'never_over_guard':False,
            'clarity_guard':{'depth_fraction':1.0},'checks':{'clarity':{'trade_floor_db':None}},**kwargs}


def test_public_balance_overrides_merge_with_processing_defaults():
    c=bm.settings({'clarity':{'min_db':10},'families':{'textures':{'kinds':['ripple']}}})
    assert c['checks']['clarity']['min_db']==10
    assert c['families']['textures']['duck_db']==-5
    assert c['families']['textures']['kinds']==['ripple']
    assert bm.DEFAULT['checks']['clarity']['min_db']==12


def test_short_silent_speech_key_and_interleaved_rates():
    a=bm.FamilyMixer(np.zeros((7,2)),sample_rate=48000)
    b=bm.FamilyMixer(np.zeros((10,2)),sample_rate=16000)
    assert np.array_equal(a.speech_key(a.voice,a.cfg['keys']['texture']),np.zeros(7))
    assert b.sr==16000 and a.sr==48000


def test_speech_key_looks_ahead_holds_and_releases():
    v=np.zeros((2*SR,2));v[SR//2:SR]=tone(seconds=.5)
    m=mixer(v);k=m.speech_key(v,m.cfg['keys']['texture'])
    assert k[round(.48*SR)]>.5
    assert k[round(1.12*SR)]>.9
    assert 0<k[round(1.7*SR)]<.2


def test_keyed_hit_uses_constant_gain_but_return_follows_speech():
    m=mixer();seg=tone(seconds=.2);key=np.linspace(0,1,len(seg))
    spec={'mix':'keyed','key':'test','gain_db':0,'duck_db':-10}
    dry,_=m.cue_mix('textures','fx',seg,0,spec,{'test':key})
    nz=abs(seg[:,0])>.01
    assert np.ptp(dry[nz,0]/seg[nz,0])<1e-12
    wet=m.bus_mix('textures','verb',seg,spec,np.zeros(len(seg)),{'test':key})
    assert np.ptp(wet[nz,0]/seg[nz,0])>.6


def test_spectral_treatment_keeps_anchor_when_spectrum_moves_peak():
    m=mixer();x=np.zeros((round(.3*SR),2))
    x[:round(.1*SR)]=tone(3500,.1,1.2)
    x[round(.2*SR):]=tone(150,.1,1.0)
    before=m.anchor_index('peak50',x)
    y,info=m._treat(x,1.,'fx',{'speech_hf':{'split_hz':600,'extra_db':-15}},800,
                    'whoosh','peak50',.1)
    assert abs(m.anchor_index('peak50',y)-before)<=round(.0001*SR)
    assert 'sync_safe' in info


def test_impact_body_ride_preserves_first_eighty_ms():
    m=mixer();x=np.ones((round(.3*SR),2))
    y=m.apply_ride(x,{'body_db':-12,'keep_attack_ms':80})
    assert np.array_equal(y[:round(.08*SR)],x[:round(.08*SR)])
    assert np.allclose(y[round(.11*SR):],10**(-12/20))


def test_joint_solver_only_rides_hits_covering_a_word(raw_cues):
    m=mixer(config=no_extra());c=[cue(),cue('outside',t=.9)]
    r=m.run(c,words=[('word',.4,.56)])
    assert 'hit' in r['rides'] and 'outside' not in r['rides']
    assert r['guard_report']['final_clarity_db']['0']>=12
    assert r['guard_report']['delivered_voice_feedback'] is False


def test_guard_counts_own_return_and_solo_matches_family(raw_cues):
    m=mixer(config=no_extra());c=cue(reverb=0)
    r=m.render_families([c],len(m.voice),0)
    a,solo,_=m.solo_mixed(c)
    expected=np.zeros_like(m.voice);expected[a:a+len(solo)]=solo
    assert np.allclose(expected,r['families']['accents'],atol=1e-10)
    assert np.max(abs(solo[len(c['samples']):]))>0
    rides,report=m.guard_rides(r,[('tail',.58,.7)],m.cfg,'clarity_guard')
    assert rides and report['rides']==1


def test_solo_return_respects_distinct_dry_and_return_silences(raw_cues):
    m=mixer(config=no_extra());c=cue(reverb=0)
    silences=[{'from':.5,'to':.7,'stems':['ui']},{'from':.7,'to':.9,'stems':['verb']}]
    r=m.render_families([c],len(m.voice),0,silences=silences)
    a,solo,_=m.solo_mixed(c)
    expected=np.zeros_like(m.voice);expected[a:a+len(solo)]=solo
    assert np.allclose(expected,r['families']['accents'],atol=1e-10)


def test_delivered_voice_feedback_changes_rides(raw_cues):
    cfg=no_extra();c=cue(level=.08);words=[('soft',.4,.56)]
    initial=mixer(config=cfg).run([c],words=words)
    calls=[]
    def limited(result,score):
        calls.append(True)
        return result['families']['voice']*.25
    out=mixer(config=cfg,delivered_voice=limited).run([c],words=words)
    assert calls and out['guard_report']['delivered_voice_feedback'] is True
    assert bm.FamilyMixer.ride_db(None,out['rides']['hit'])<bm.FamilyMixer.ride_db(None,initial['rides']['hit'])
    assert len(out['guard_report']['rounds'])<=3


def test_delivered_voice_feedback_solves_an_initially_clear_pre_master_hit(raw_cues):
    c=cue(level=.001);words=[('word',.4,.56)];cfg=no_extra()
    assert mixer(config=cfg).run([c],words=words)['rides']=={}
    calls=[]
    def limited(result,score):
        calls.append(True)
        return result['families']['voice']*.01
    out=mixer(config=cfg,delivered_voice=limited).run([c],words=words)
    assert calls and out['rides']
    assert out['guard_report']['final_clarity_db']['0']>=12
    assert len(out['guard_report']['rounds'])<=3


def test_half_depth_keeps_actual_top_and_whole_split(raw_cues):
    m=mixer(config=no_extra(clarity_guard={'depth_fraction':.5}))
    r=m.run([cue()],words=[('word',.4,.56)])
    assert r['half_depth']==['hit']
    assert r['rides']['hit']==m.scale_ride(r['rides_full']['hit'],.5)
    row=r['ride_rows'][0]
    assert row['half_depth'] and not row['pinned']
    a,before=r['solo_hit']('hit',baseline=True);b,after=r['solo_hit']('hit')
    assert a==b and np.max(abs(before-after))>0


def test_frozen_hit_keeps_exact_ride_even_when_word_cannot_clear(raw_cues):
    m=mixer(config=no_extra());fixed={'top_db':-1,'split':600,'whole_db':0}
    r=m.run([cue(level=1.)],words=[('word',.4,.56)],pinned={'hit':fixed},frozen={'hit'})
    assert r['rides']['hit']==fixed
    assert r['guard_report']['final_clarity_db']['0']<12
    assert r['ride_rows'][0]['pinned']
    assert r['frozen']==['hit']


def test_never_over_caps_extra_ride_and_reports_unresolved(raw_cues):
    cfg={'clarity_guard':False,'air_guard':False}
    m=mixer(config=cfg);r=m.run([cue(level=10)],words=[('word',.4,.56)])
    rep=r['guard_report']['never_over_guard']
    assert rep['rides']['hit']==-12
    assert rep['rounds'][-1]['over']
    assert r['guard_report']['final_clarity_db']['0']<0


def test_families_sum_to_stems_and_inputs_are_unchanged(raw_cues):
    m=mixer(config=no_extra());c=cue(reverb=-15);original=c['samples'].copy()
    r=m.run([c],words=[('word',.4,.56)])
    effects=sum(v for k,v in r['families'].items() if k!='voice')
    assert np.allclose(effects,sum(r['stems'].values()),atol=1e-12)
    assert np.array_equal(c['samples'],original)


def approved():
    old={'id':'source','event':'step','t':.4,'beat':.4,'type':'wood','family':'accents'}
    new={**old,'id':'current','approved':'refs/one'}
    sheet={'music':{'match':[{'clip':'refs/one','from':0,'to':1,'core':[.2,.8],
                             'pin_cues':True,'pin_rides':True}]}}
    ref={'cue_sheet':{'window':[0,1],'cues':[old]},'balance_used':{'rides':{'source':-3.25}}}
    return sheet,new,ref


def test_approved_reference_requires_resolver_and_freezes_exact_rides():
    sheet,c,ref=approved()
    with pytest.raises(Refused,match='resolver'):
        bm.resolve_approved(sheet,[c])
    pinned,frozen,full=bm.resolve_approved(sheet,[c],lambda clip:ref)
    assert pinned=={'current':-3.25} and frozen=={'current'} and full==set()
    ref['balance_used']['rides']={}
    assert bm.resolve_approved(sheet,[c],lambda clip:ref)[1]=={'current'}


def test_pin_cues_is_full_depth_and_not_frozen():
    sheet,c,ref=approved();sheet['music']['match'][0]['pin_rides']=False
    pinned,frozen,full=bm.resolve_approved(sheet,[c],lambda clip:ref)
    assert pinned=={} and frozen==set() and full=={'current'}


def test_cue_only_approval_resolves_full_depth_around_changed_narration(raw_cues):
    current=cue('current',level=.3)
    source={**current,'id':'source'}
    current['approved']='ref'
    sheet={'cues':[current],'music':{'match':[{'clip':'ref','from':0,'to':1.2,
                                             'pin_cues':True,'pin_rides':False}]}}
    ref={'cue_sheet':{'window':[0,1.2],'cues':[source]},'balance_used':{'rides':{'source':-1}}}
    cfg=no_extra(clarity_guard={'depth_fraction':.5})
    def render(level):
        voice=tone(level=level)
        return bm.mix([current],len(voice),voice=voice,words=[('word',.4,.56)],sample_rate=SR,
                      config=cfg,sheet=sheet,load_reference=lambda clip:ref)
    strong,polished=render(.15),render(.05)
    assert polished['full_depth']==['current'] and not polished['half_depth']
    assert not polished['frozen'] and not polished['pinned']
    assert polished['rides']==polished['rides_full']
    assert bm.FamilyMixer.ride_db(None,polished['rides']['current']) < bm.FamilyMixer.ride_db(None,strong['rides']['current'])


@pytest.mark.parametrize('field,value',[('family','textures'),('stem','tonal'),('anchor','end'),('beat_end',.8)])
def test_approved_routing_and_anchor_changes_refuse(field,value):
    sheet,c,ref=approved()
    c.update(stem='ui',anchor='onset')
    ref['cue_sheet']['cues'][0].update(stem='ui',anchor='onset')
    c[field]=value
    with pytest.raises(Refused,match='family, stem or anchor'):
        bm.resolve_approved(sheet,[c],lambda clip:ref)


def test_legacy_approved_family_comes_only_from_bound_balance():
    sheet,c,ref=approved()
    old=ref['cue_sheet']['cues'][0]
    old.pop('family');old['p3kind']='tick';c['p3kind']='tick'
    ref['balance_used']['families']={'accents':{'kinds':['tick']}}
    assert bm.resolve_approved(sheet,[c],lambda clip:ref)[0]=={'current':-3.25}
    assert 'family' not in old
    c['family']='textures'
    with pytest.raises(Refused,match='family, stem or anchor'):
        bm.resolve_approved(sheet,[c],lambda clip:ref)


@pytest.mark.parametrize('mapping',[{}, {'accents':{'kinds':['tick']},'textures':{'kinds':['tick']}}])
def test_missing_or_ambiguous_approved_family_refuses(mapping):
    sheet,c,ref=approved()
    old=ref['cue_sheet']['cues'][0]
    old.pop('family');old['p3kind']='tick';c['p3kind']='tick'
    ref['balance_used']['families']=mapping
    with pytest.raises(Refused,match='unique approval-bound'):
        bm.resolve_approved(sheet,[c],lambda clip:ref)


@pytest.mark.parametrize('declared',[{}, {'family':'voice','type':'vo'}, {'role':'bed'}, {'p3kind':'landing'}])
def test_extra_unapproved_cue_cannot_hide_in_protected_span(declared):
    sheet,c,ref=approved()
    extra={**c,**declared,'id':'extra','event':'extra','t':.6,'beat':.6}
    extra.pop('approved')
    sheet['cues']=[c,extra]
    # Check the full sheet even when the renderer has already excluded this extra.
    with pytest.raises(Refused,match='missing or adding'):
        bm.resolve_approved(sheet,[c],lambda clip:ref,composed_score=True)


def test_source_supported_voice_and_bed_exclusions_are_explicit():
    sheet,c,ref=approved()
    voice={'id':'voice','event':'voice','t':.1,'beat':.1,'family':'voice','type':'vo'}
    bed={'id':'source-bed','event':'bed','t':.2,'beat':.2,'family':'score','type':'spad','p3kind':'bed'}
    ref['cue_sheet']['cues'].extend([voice,bed])
    ref['balance_used']['rides']['source-bed']=-4
    current_bed={**bed,'id':'current-bed','approved':'refs/one'}
    sheet['cues']=[c,voice,current_bed]
    assert bm.resolve_approved(sheet,[c],lambda clip:ref,composed_score=True)[0]=={'current':-3.25}
    with pytest.raises(Refused,match='missing or adding'):
        bm.resolve_approved(sheet,[c],lambda clip:ref)
    current_bed['p3kind']='tick'
    with pytest.raises(Refused,match='sound parameters'):
        bm.resolve_approved(sheet,[c],lambda clip:ref,composed_score=True)


def test_known_source_rides_outside_a_narrow_pinned_span_are_not_applied():
    sheet,c,ref=approved()
    sheet['music']['match'][0]['to']=.8
    outside={**ref['cue_sheet']['cues'][0],'id':'outside','event':'outside','t':.9,'beat':.9}
    ref['cue_sheet']['cues'].append(outside)
    ref['balance_used']['rides']['outside']=-6
    assert bm.resolve_approved(sheet,[c],lambda clip:ref)[0]=={'current':-3.25}


@pytest.mark.parametrize('change',['beat','missing','duplicate','unknown_ride'])
def test_approved_reference_mismatch_refuses(change):
    sheet,c,ref=approved();cues=[c]
    if change=='beat': c['beat']=.5
    if change=='missing': cues=[]
    if change=='duplicate': cues=[c,dict(c,id='other')]
    if change=='unknown_ride': ref['balance_used']['rides']={'nope':-3}
    with pytest.raises(Refused): bm.resolve_approved(sheet,cues,lambda clip:ref)


def test_score_match_uses_core_and_preserves_other_spans():
    voice=tone(seconds=4);score=tone(120,4,.05)
    ref={'cue_sheet':{'window':[1,3]},'voice':voice[SR:3*SR], 'score':score[SR:3*SR]*.5}
    matches=[{'clip':'ref','from':1,'to':3,'core':[2,2.8]}]
    out,report=bm.score_match(score,voice,matches,lambda clip:ref,sample_rate=SR)
    assert np.array_equal(out[:SR],score[:SR])
    assert np.array_equal(out[round(3.1*SR):],score[round(3.1*SR):])
    assert abs(report[0]['trim_db']+6.02)<.1
    assert abs(report[0]['after_lu']-report[0]['reference_score_lu_vs_voice'])<.1


def test_score_match_rejects_unmeasurable_or_outside_core():
    voice=tone();score=tone(120)
    ref={'cue_sheet':{'window':[0,1.2]},'voice':voice,'score':score}
    with pytest.raises(Refused):
        bm.score_match(score,voice,[{'clip':'ref','from':0,'to':1,'core':[1,2]}],lambda clip:ref,sample_rate=SR)


def test_half_depth_floor_deepens_only_covering_hits(raw_cues):
    cfg=no_extra(clarity_guard={'depth_fraction':.5},checks={'clarity':{'trade_floor_db':6}})
    m=mixer(config=cfg)
    r=m.run([cue(level=.6),cue('outside',t=.9)],words=[('word',.4,.56)])
    floor=r['guard_report']['trade_floor']
    assert floor['depth_fraction_now']['hit']>.5
    assert 'outside' not in floor['depth_fraction_now']
    assert r['guard_report']['final_clarity_db']['0']>=6


def test_score_is_lowered_in_masking_low_band(raw_cues):
    m=mixer(config={'clarity_guard':False,'air_guard':False,'never_over_guard':False})
    score=tone(150,level=.4)
    r=m.run([cue(hz=150,level=.015)],score=score)
    rep=r['guard_report']['score_accents']
    assert rep['hits_dipped']==1
    assert rep['weakest_after_db']>rep['weakest_before_db']
    assert np.max(abs(r['score']))<=np.max(abs(score))+1e-5


def test_layered_count_children_have_guard_identity(raw_cues):
    parent={'id':'count','event':'count','family':'accents','stem':'ui','kind':'count','t':.4,
            'notes':[cue('a'),cue('b',t=.7)]}
    r=mixer(config=no_extra()).run([parent],words=[('word',.4,.56)])
    assert 'count.1' in r['rides']
    assert all(row[0].startswith('count.') for row in r['kept'])


def test_unridden_frozen_event_cannot_disappear():
    sheet,c,ref=approved()
    ref['cue_sheet']['cues'].append({'id':'second','event':'other','t':.6,'beat':.6,'type':'wood','family':'accents'})
    with pytest.raises(Refused,match='missing or adding'):
        bm.resolve_approved(sheet,[c],lambda clip:ref)


def test_frozen_source_numeric_reference(raw_cues):
    """Expectations were captured from the approved source functions, never from this port."""
    import json
    from pathlib import Path
    ref=json.loads((Path(__file__).parents[1]/'fixtures/effects_balance_reference.json').read_text())
    sr=ref['sample_rate'];time=np.arange(sr)/sr
    voice=np.repeat((.12*np.sin(2*np.pi*2400*time)*((time>.12)&(time<.8)))[:,None],2,axis=1)
    cues=[]
    for item in ref['inputs']:
        t=np.arange(round(item['seconds']*sr))/sr
        samples=np.repeat((item['level']*np.sin(2*np.pi*item['hz']*t)*np.exp(-10*t))[:,None],2,axis=1)
        cues.append({'id':item['id'],'event':item['id'],'family':'accents','stem':'ui','type':'wood',
                     't':item['t'],'beat':item['t'],'anchor':'onset','samples':samples,'reverb':item['reverb']})
    m=bm.FamilyMixer(voice,sample_rate=sr,fps=30000/1001)
    r=m.render_families(cues,sr,0)
    indices=ref['key_samples']['indices']
    for name in ('texture','swell'):
        assert np.allclose(r['keys'][name][indices],ref['key_samples'][name],rtol=1e-12,atol=1e-12)
    y,info=m.cue_mix('textures','fx',cues[0]['samples'],round(.25*sr),m.cfg['families']['textures'],r['keys'],
                    'whoosh','peak50',10)
    assert info==ref['cue_treatment']['applied']
    assert float((y*y).sum())==pytest.approx(ref['cue_treatment']['energy'],rel=1e-11)
    assert np.allclose(y[[0,300,1500,4000,7000],0],ref['cue_treatment']['samples'],atol=1e-12)
    for name,frozen in [('all',()),('pinned',('lead',))]:
        rides,report=m.guard_rides(r,ref['words'],m.cfg,'clarity_guard',frozen=frozen)
        assert rides==ref['guards'][name]['rides']
        assert report==ref['guards'][name]['report']


def test_approved_gesture_children_use_explicit_order_and_keep_exact_rides():
    sheet,c,ref=approved()
    notes=[{'t':.4,'beat':.4},{'t':.6,'beat':.6}]
    ref['cue_sheet']['cues'][0]['notes']=copy.deepcopy(notes)
    ref['balance_used']['rides']={'source.1':-2,'source.2':-5}
    c['notes']=copy.deepcopy(notes)
    pinned,frozen,full=bm.resolve_approved(sheet,[c],lambda clip:ref)
    assert pinned=={'current.1':-2,'current.2':-5}
    assert frozen=={'current.1','current.2'} and not full


def test_no_voice_does_not_apply_meaningless_voice_relative_guards(raw_cues):
    m=mixer(np.zeros((SR,2)))
    r=m.run([cue()],words=[('unvoiced',.4,.56)])
    assert r['rides']=={} and r['guard_report']['voice_available'] is False


def test_44100_key_uses_real_millisecond_control_clock():
    sr=44100;t=np.arange(sr)/sr
    v=np.repeat((.1*np.sin(2*np.pi*1000*t)*((t>.3)&(t<.6)))[:,None],2,axis=1)
    m=bm.FamilyMixer(v,sample_rate=sr)
    key=m.speech_key(v,m.cfg['keys']['texture'])
    assert key.shape==(sr,) and key[round(.28*sr)]>.5
    assert key[round(.72*sr)]>.9


def test_approved_marker_does_not_authorize_changed_gain_or_take():
    for field in ('gain','seed','take','t','reverb'):
        sheet,c,ref=approved();c[field]=.25
        with pytest.raises(Refused,match='sound parameters'):
            bm.resolve_approved(sheet,[c],lambda clip:ref)


def test_approved_legacy_pad_pitch_list_normalizes_without_becoming_gesture():
    from slate.sound import checks
    sheet,c,ref=approved()
    old=ref['cue_sheet']['cues'][0]
    old.update(type='spad',notes=['D3','F3','A3'])
    c.update(type='spad',params={'notes':['D3','F3','A3']})
    wave=tone(seconds=.2)
    ref['render_log']={'sample_rate':SR,'cues':[{'hit':'source','waveform_identity':checks.hit_hash(wave)}]}
    assert bm.resolve_approved(sheet,[c],lambda clip:ref,
                               render_cue=lambda cue:(wave,0),sample_rate=SR)[0]=={'current':-3.25}


def test_mixed_sync_reports_actual_master_anchor_and_keeps_pins(raw_cues):
    m=mixer(config=no_extra());fixed={'top_db':-1,'split':600,'whole_db':0}
    r=m.run([cue()],words=[('word',.4,.56)],pinned={'hit':fixed},frozen={'hit'})
    before=r['measure_sync']()
    assert before['scope']=='pre_master' and before['rows'][0]['within_frame']
    calls=[]
    def displaced(start,samples,c):
        calls.append(c['id'])
        return start+round(.05*SR),samples
    after=r['measure_sync'](displaced)
    assert after['scope']=='master' and after['over'][0]['id']=='hit'
    assert after['threshold_ms']==pytest.approx(1000/30)
    assert calls==['hit','hit'] and r['rides']['hit']==fixed


def test_absent_mixed_hit_is_unmeasured_not_passed(raw_cues):
    r=mixer(config=no_extra()).run([cue(t=2.)])
    proof=r['measure_sync']()
    assert proof['unmeasured'][0]['id']=='hit' and not proof['rows']


def test_score_match_refuses_silent_reference_core():
    voice=tone();score=tone(120)
    ref={'cue_sheet':{'window':[0,1.2]},'voice':voice*0,'score':score}
    with pytest.raises(Refused,match='measurable'):
        bm.score_match(score,voice,[{'clip':'ref','from':0,'to':1.2,'core':[.2,1.]}],lambda clip:ref,sample_rate=SR)


def test_hall_tail_does_not_redefine_dry_end_anchor(raw_cues):
    c=cue(reverb=0);c.update(anchor='end',beat=c['t']+c['dur'])
    r=mixer(config=no_extra()).run([c])
    report=r['measure_sync']()
    assert report['signal']=='dry_hit' and report['rows'][0]['within_frame']
    _,dry=r['solo_hit']('hit',include_return=False)
    _,wet=r['solo_hit']('hit')
    assert len(wet)>len(dry)


def test_legacy_counter_reports_schedule_and_audibility_without_claiming_waveform_anchor(raw_cues):
    c=cue(seconds=.2,t=.2)
    c.update(type='countup3',kind='count',anchor='clicks',beat=.24,beat_end=.4,
             params={'from':0,'to':5,'hold_frames':2,'scale':[220,330,440],'kind':'block','ease':'linear'})
    m=bm.FamilyMixer(tone(),sample_rate=SR,fps=50,
                     config={'clarity_guard':False,'air_guard':False,'never_over_guard':False})
    r=m.run([c]);report=r['measure_sync']()
    row=report['rows'][0]
    assert row['signal']=='scheduled_clicks' and row['clicks']==5
    assert row['before_anchor_s']==row['after_anchor_s']==.24
    assert row['end_anchor_s']==row['beat_end']==.4
    assert row['within_frame'] and row['audible_after']
    def muted(a,y,cue): return a,y*0
    assert r['measure_sync'](muted)['unmeasured'][0]['id']=='hit'


def test_legacy_counter_schedule_uses_its_films_named_motion_curve(raw_cues,monkeypatch,tmp_path):
    from types import SimpleNamespace
    from slate.sound import film
    c=cue(seconds=.2,t=.2)
    c.update(type='countup3',kind='count',anchor='clicks',beat=.24,beat_end=.4,
             params={'from':0,'to':5,'hold_frames':2,'scale':[220,330,440],'kind':'block','ease':'counter-ease'})
    m=bm.FamilyMixer(tone(),sample_rate=SR,fps=50,
                    config={'clarity_guard':False,'air_guard':False,'never_over_guard':False})
    r=m.run([c])
    assert r['measure_sync']()['unmeasured']
    loaded=[]
    def load(path):
        loaded.append(path)
        return SimpleNamespace(brand={'motion':{'eases':{'counter-ease':{'kind':'linear'}}}})
    monkeypatch.setattr(film,'load',load)
    m.film_dir=tmp_path
    report=r['measure_sync']()
    assert loaded==[tmp_path] and not report['unmeasured']
    assert report['rows'][0]['within_frame'] and report['rows'][0]['clicks']==5


def test_end_aligned_legacy_counter_uses_its_rendered_placement():
    c={'id':'counter','event':'counter','type':'countup3','kind':'count','family':'accents','stem':'ui',
       't':.8,'beat':.34,'beat_end':.5,'anchor':'clicks','gain':-24,
       'params':{'dur':.2,'from':0,'to':5,'hold_frames':2,'scale':[220,330,440],
                 'kind':'block','ease':'linear','align':'end','fps_num':50,'fps_den':1}}
    cfg={'clarity_guard':False,'air_guard':False,'never_over_guard':False}
    m=bm.FamilyMixer(tone(),sample_rate=SR,fps=50,config=cfg)
    report=m.run([c])['measure_sync']()
    assert report['rows'][0]['before_anchor_s']==.34
    assert report['rows'][0]['end_anchor_s']==.5
    assert report['rows'][0]['within_frame']
    c.update(beat=.84,beat_end=1.)
    bad=m.run([c])['measure_sync']()
    assert not bad['rows'][0]['within_frame'] and bad['over']


def test_approved_log_binds_current_dry_waveform():
    from slate.sound import checks
    sheet,c,ref=approved();wave=tone(seconds=.2)
    ref['render_log']={'sample_rate':SR,'cues':[{'hit':'source','waveform_identity':checks.hit_hash(wave)}]}
    good=bm.resolve_approved(sheet,[c],lambda clip:ref,render_cue=lambda cue:(wave,0),sample_rate=SR)
    assert good[0]=={'current':-3.25}
    with pytest.raises(Refused,match='approved waveform'):
        bm.resolve_approved(sheet,[c],lambda clip:ref,render_cue=lambda cue:(tone(700,.2),0),sample_rate=SR)


@pytest.mark.parametrize('change',['missing','duplicate','sample_rate','renderer'])
def test_approved_wave_records_require_complete_unambiguous_evidence(change):
    from slate.sound import checks
    sheet,c,ref=approved();wave=tone(seconds=.2)
    row={'hit':'source','waveform_identity':checks.hit_hash(wave)}
    ref['render_log']={'sample_rate':SR,'cues':[row]}
    render=lambda cue:(wave,0)
    if change=='missing':ref['render_log']['cues']=[]
    if change=='duplicate':ref['render_log']['cues'].append(dict(row))
    if change=='sample_rate':ref['render_log']['sample_rate']=48000
    if change=='renderer':render=None
    with pytest.raises(Refused):
        bm.resolve_approved(sheet,[c],lambda clip:ref,render_cue=render,sample_rate=SR)


@pytest.mark.parametrize('voice',[
    {'type':'sample','file':'approved.wav'},
    {'type':'snote','pitch':'A4','role':'felt_note'},
    {'type':'spad','params':{'notes':['A3','C4','E4']}},
    {'type':'whoosh','ease':'brand-curve','dur':.2},
    {'type':'layered','params':{'layers':[{'type':'snote','pitch':'A4','role':'felt_note'}]}}])
def test_legacy_approval_refuses_unbound_sample_and_brand_curve_inputs(voice):
    sheet,c,ref=approved()
    ref['cue_sheet']['cues'][0].update(copy.deepcopy(voice));c.update(copy.deepcopy(voice))
    with pytest.raises(Refused,match='unbound sample or motion'):
        bm.resolve_approved(sheet,[c],lambda clip:ref)


def test_legacy_builtin_curve_is_independent_of_brand_name_shadowing():
    from slate.sound.effects import voices
    sheet,c,ref=approved()
    for item in (c,ref['cue_sheet']['cues'][0]):
        item.update(type='whoosh',ease='linear',dur=.2)
    assert bm.resolve_approved(sheet,[c],lambda clip:ref)[0]=={'current':-3.25}
    progress=np.linspace(0,1,9)
    motion={'eases':{'linear':'brand-curve'}}
    assert np.array_equal(voices.ease('linear',progress,motion),progress)


def test_nested_params_cannot_mask_changed_absolute_cue_placement():
    sheet,c,ref=approved()
    ref['cue_sheet']['cues'][0]['params']={'t':.4};c['params']={'t':.4};c['t']=.5
    with pytest.raises(Refused,match='sound parameters'):
        bm.resolve_approved(sheet,[c],lambda clip:ref)


@pytest.mark.parametrize('field',['hp','lp'])
def test_sample_filter_changes_require_new_approval(field):
    sheet,c,ref=approved()
    for item in (c,ref['cue_sheet']['cues'][0]):
        item.update(type='sample',file='approved.wav')
    c[field]=500
    with pytest.raises(Refused,match='sound parameters'):
        bm.resolve_approved(sheet,[c],lambda clip:ref)
