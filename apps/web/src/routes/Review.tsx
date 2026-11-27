import { useEffect, useRef, useState } from 'react';
type Segment = { id: string; start_ms: number; end_ms: number; en: string; zh: string };
type ReviewData = { media_url: string; segments: Segment[]; report: { revision_id: string; segment_count: number; sample_ids: string[]; issues: { segment_id: string; start_ms: number; flags: string[] }[]; offset_ms: number; scale: number } };
const time = (ms: number) => `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}.${String(Math.floor(ms % 1000)).padStart(3,'0')}`;
export function Review() {
  const [versions, setVersions] = useState<{id: string; edition: string}[]>([]);
  const [data, setData] = useState<ReviewData>();
  const [error, setError] = useState('');
  const [position, setPosition] = useState(0);
  const [selected, setSelected] = useState<Segment>();
  const [version, setVersion] = useState('');
  const [offset, setOffset] = useState(0);
  const [audio, setAudio] = useState({ muted: false, volume: 1 });
  const video = useRef<HTMLVideoElement>(null);
  async function load(id: string) {
    setVersion(id); setError(''); setData(undefined); setSelected(undefined);
    try {
      const response = await fetch(`/api/v1/review/${id}`);
      if (!response.ok) throw new Error();
      setData(await response.json());
    } catch { setError('无法读取审核数据，请确认后端已启动并完成导入。'); }
  }
  useEffect(() => {
    fetch('/api/v1/review/versions').then(r => { if (!r.ok) throw new Error(); return r.json(); }).then(rows => { setVersions(rows); if (rows.length) void load(rows[0].id); }).catch(() => setError('审核服务不可用，请启动新版后端。'));
  }, []);
  function play(segment: Segment) {
    setSelected(segment);
    if (video.current) { video.current.pause(); video.current.currentTime = Math.max(0, (segment.start_ms + offset) / 1000 - 0.7); }
  }
  const current = data?.segments.filter(s => s.start_ms + offset <= position && position < s.end_ms + offset) ?? [];
  return <main>
    <header><h1>双语字幕抽查</h1><p>此页面仅用于本地审核，不代表素材已公开发布。核对对白内容、翻译与声音同步。</p><a href="/">返回首页</a></header>
    <select aria-label="选择审核视频" value={version} onChange={e => void load(e.target.value)}>{versions.map(v => <option key={v.id} value={v.id}>{v.edition}</option>)}</select>
    {error && <p role="alert">{error}</p>}
    {!data && !error && <p>正在读取云端审核数据…</p>}
    {data && <>
      <section><p role="status">当前素材待同步审核。请先按原始时间抽查；临时偏移仅用于对照，不保存，不代表整集已修复。</p>
      <label>临时字幕偏移（毫秒；负数提前）：<input type="number" step={100} value={offset} onChange={e => { const value=Number(e.target.value); if (Number.isFinite(value)) setOffset(value); setSelected(undefined); }} /></label>
      <p><button onClick={() => {setOffset(0);setSelected(undefined);}}>恢复原始时间</button></p>
      <video ref={video} src={data.media_url} controls preload="metadata" style={{width: '100%'}} onVolumeChange={() => {if(video.current) setAudio({muted:video.current.muted,volume:video.current.volume});}} onSeeked={() => {if(selected) void video.current?.play().catch(() => setError('请点击播放按钮。'));}} onError={() => setError('视频加载失败；请检查网络，或点击刷新视频链接。')} onTimeUpdate={() => { const ms = (video.current?.currentTime ?? 0) * 1000; setPosition(ms); if (selected && !video.current?.seeking && ms > selected.end_ms + offset + 800) video.current?.pause(); }} />
      <p><button onClick={() => {const player=video.current;if(player){player.muted=false;player.volume=1;setSelected(undefined);void player.play().catch(() => setError('播放未成功，请检查浏览器提示后重试。'));}}}>开启声音并播放</button> 当前声音：{audio.muted || audio.volume === 0 ? '静音' : `音量 ${Math.round(audio.volume*100)}%`}</p>
        <div aria-live="polite" style={{minHeight: 100, textAlign: 'center'}}>{current.map(s => <div key={s.id}><p style={{margin: 4}}>{s.zh}</p><p style={{margin: 4}}>{s.en}</p></div>)}</div>
        <p>当前位置：{time(position)} · 选中：{selected ? `${time(selected.start_ms)}—${time(selected.end_ms)}` : '尚未选择'}</p>
        <button onClick={() => void load(version)}>刷新视频链接</button>
      </section>
      <section><h2>20 个分散抽查点</h2><p>依次点击，覆盖开头、中间和结尾。记录时间点，以及提前、延后、缺句或翻译错配情况。</p><div style={{display:'flex',flexWrap:'wrap',gap:8}}>{data.report.sample_ids.map(id => { const s = data.segments.find(x => x.id === id); return s ? <button key={id} onClick={() => play(s)}>{time(s.start_ms)}</button> : null; })}</div></section>
      <section><h2>待关注片段（{data.report.issues.length}）</h2><p>缺失语言或多句合并并不一定是错误，需要人工确认。</p>{data.report.issues.map(issue => { const s = data.segments.find(x => x.id === issue.segment_id); const labels: Record<string,string> = {missing_language:'缺少一种语言',many_to_many_review:'多条字幕合并',long_segment:'片段较长',boundary_difference:'中英边界差异'}; return s ? <p key={s.id}><button onClick={() => play(s)}>{time(s.start_ms)}</button> {issue.flags.map(f => labels[f] ?? f).join('、')}<br/>{s.zh}<br/>{s.en}</p> : null; })}</section>
      <section><h2>全部台词（{data.segments.length}）</h2><details><summary>展开查看与定位</summary>{data.segments.map(s => <p key={s.id}><button onClick={() => play(s)}>{time(s.start_ms)}</button> {s.zh}<br/>{s.en}</p>)}</details></section>
    </>}
  </main>;
}
