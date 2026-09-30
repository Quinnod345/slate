// A placeholder act: every shot of an act as a labelled box with its timing, so the act renders from day one and a
// builder always has a first working version on disk. It shows the shot, its frames and timecode, its on-screen text,
// its read with the word being said picked out, a progress bar through the shot, and the act's shots as a strip with
// the playhead. The engine's generated entry uses it for any act whose scene isn't written yet.
import {AbsoluteFill} from 'remotion';
import {actShots, actSpan, hasWords, secOf, shot as shotOf, timecode, voLead, filmData, type Shot} from './core/film';
import {clamp01} from './core/spline';
import {useFilmFrame, useFilmTime, useFrameSize, useTokens, useType} from './context';
import {Ground} from './paper';

function currentShot(list: Shot[], frame: number): Shot | null {
  let hit: Shot | null = list[0] ?? null;
  for (const s of list) if (s.frameIn <= frame) hit = s;
  return hit;
}

/** The read of a shot with the word being said at t picked out (from vo_words; the plain line when there are none). */
function Read({id, t}: {id: string; t: number}) {
  const tk = useTokens();
  const s = useType('body', tk.color.ink3);
  const words = filmData()?.vo_words?.shots?.[id]?.words ?? [];
  const sh = shotOf(id);
  if (!hasWords(id)) return <div style={s}>{sh.vo}</div>;
  const base = sh.start + voLead(id);
  return (
    <div style={s}>
      {words.map((w, i) => {
        const on = t >= base + w.start && t < base + w.end;
        const said = t >= base + w.end;
        return (
          <span key={i} style={{color: on ? tk.color.accentInk : said ? tk.color.ink2 : undefined, fontWeight: on ? 700 : undefined}}>
            {w.w}
            {i < words.length - 1 ? ' ' : ''}
          </span>
        );
      })}
    </div>
  );
}

export function PlaceholderAct({act, note}: {act: number | string; note?: string}) {
  const t = useFilmTime();
  const frame = useFilmFrame();
  const {width, height, short} = useFrameSize();
  const tk = useTokens();
  const eyebrow = useType('eyebrow');
  const h1 = useType('h1', tk.color.ink);
  const h2 = useType('h2', tk.color.ink2);
  const mono = useType('mono');
  const list = actShots(act);
  const span = actSpan(act);
  const cur = currentShot(list, Math.floor(frame));
  if (!cur || !span) {
    return (
      <AbsoluteFill>
        <Ground />
        <div style={{...h2, position: 'absolute', left: 0.08 * width, top: 0.1 * height}}>Act {String(act)} has no shots in timing/shots.json.</div>
      </AbsoluteFill>
    );
  }
  const pad = 0.07 * short;
  const within = clamp01((frame - cur.frameIn) / Math.max(1, cur.frames));
  const stripY = height - pad - 0.05 * short;
  const stripW = width - 2 * pad;
  return (
    <AbsoluteFill>
      <Ground />
      <div style={{position: 'absolute', left: pad, top: pad, right: pad}}>
        <div style={eyebrow}>
          Act {String(act)} · {span.first}–{span.last} · placeholder{note ? ` · ${note}` : ''}
        </div>
        <div style={{...h1, marginTop: 0.02 * short}}>{cur.id}</div>
        <div style={{...mono, marginTop: 0.012 * short}}>
          frames {cur.frameIn}–{cur.frameOut - 1} · {timecode(cur.frameIn)} · {cur.dur.toFixed(2)} s · now {timecode(Math.floor(frame))}
        </div>
        {cur.onScreen && <div style={{...h2, marginTop: 0.03 * short}}>{cur.onScreen}</div>}
        <div style={{marginTop: 0.03 * short, maxWidth: 0.8 * width}}>
          <Read id={cur.id} t={t} />
        </div>
      </div>
      <div style={{position: 'absolute', left: pad, top: stripY - 0.02 * short, width: stripW, height: 0.006 * short, background: tk.color.hairline, borderRadius: short}}>
        <div style={{width: `${within * 100}%`, height: '100%', background: tk.color.accent, borderRadius: short}} />
      </div>
      <div style={{position: 'absolute', left: pad, top: stripY, width: stripW, height: 0.05 * short, display: 'flex', gap: 0.004 * short}}>
        {list.map((s) => {
          const active = s.id === cur.id;
          return (
            <div
              key={s.id}
              style={{
                ...mono,
                flex: `${s.frames} 0 0`,
                background: active ? tk.color.softAccent : tk.color.surface,
                boxShadow: `inset 0 0 0 ${Math.max(1, 0.0012 * short)}px ${active ? tk.color.accent : tk.color.hairline}`,
                borderRadius: 0.006 * short,
                display: 'flex',
                alignItems: 'center',
                paddingLeft: 0.008 * short,
                overflow: 'hidden',
                whiteSpace: 'nowrap',
                color: active ? tk.color.accentInk : tk.color.ink3,
              }}
            >
              {s.id}
            </div>
          );
        })}
      </div>
      <div style={{position: 'absolute', left: pad + ((frame - span.frameIn) / Math.max(1, span.frames)) * stripW, top: stripY - 0.012 * short, width: Math.max(1, 0.0015 * short), height: 0.07 * short, background: tk.color.ink}} />
      <div style={{...mono, position: 'absolute', right: pad, top: pad}}>{secOf(Math.floor(frame)).toFixed(2)} s</div>
    </AbsoluteFill>
  );
}
