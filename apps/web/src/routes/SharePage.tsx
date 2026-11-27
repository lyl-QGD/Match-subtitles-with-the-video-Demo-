import { useEffect, useRef, useState } from 'react';
import { ApiError, SharedClip, resolveShare } from '../lib/api';

function formatTime(ms: number) {
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.floor((ms % 60_000) / 1000);
  const millis = ms % 1000;
  return `${minutes}:${String(seconds).padStart(2, '0')}.${String(millis).padStart(3, '0')}`;
}

function shareErrorMessage(error: unknown) {
  if (error instanceof ApiError) {
    if (error.code === 'share_invalid') return '这个分享链接无效。请向分享者索取新的链接。';
    if (error.code === 'share_revoked') return '这个分享链接已被撤销。';
    if (error.code === 'share_expired') return '这个分享链接已过期。请向分享者索取新的链接。';
    if (error.code === 'share_unavailable') return '对应素材或分享授权已不可用。';
    if (error.code === 'database_unavailable') return '云端数据库暂时无法连接，请稍后重试。';
  }
  return error instanceof Error ? error.message : '分享内容暂时无法加载。';
}

export function SharePage({ token }: { token: string }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const resumeTimeRef = useRef<number | null>(null);
  const retryCountRef = useRef(0);
  const refreshingRef = useRef(false);
  const [data, setData] = useState<SharedClip | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [currentTimeMs, setCurrentTimeMs] = useState(0);
  const [mediaRevision, setMediaRevision] = useState(0);

  useEffect(() => {
    let active = true;
    setData(null);
    setError('');
    setLoading(true);
    setCurrentTimeMs(0);
    retryCountRef.current = 0;
    resolveShare(token).then(result => {
      if (!active) return;
      setData(result);
      setCurrentTimeMs(result.start_ms);
    }).catch(cause => {
      if (active) setError(shareErrorMessage(cause));
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [token]);

  async function refreshMediaAddress() {
    if (!data || refreshingRef.current) return;
    if (retryCountRef.current >= 1) {
      setError('播放地址刷新后仍无法加载，请稍后重新打开分享链接。');
      return;
    }
    retryCountRef.current += 1;
    refreshingRef.current = true;
    resumeTimeRef.current = videoRef.current?.currentTime ?? data.start_ms / 1000;
    try {
      const refreshed = await resolveShare(token);
      if (refreshed.version_id !== data.version_id || refreshed.revision_id !== data.revision_id) {
        setError('分享内容版本已变化，请重新打开分享链接。');
        return;
      }
      setData(refreshed);
      setMediaRevision(value => value + 1);
      setError('');
    } catch (cause) {
      setError(shareErrorMessage(cause));
    } finally {
      refreshingRef.current = false;
    }
  }

  function constrainPlayback(video: HTMLVideoElement) {
    if (!data) return;
    const start = data.start_ms / 1000;
    const end = data.end_ms / 1000;
    setCurrentTimeMs(Math.floor(video.currentTime * 1000));
    if (video.currentTime < start - 0.25) video.currentTime = start;
    if (video.currentTime >= end) {
      video.pause();
      video.currentTime = end;
      setCurrentTimeMs(data.end_ms);
    }
  }

  const activeCaption = data?.segments.find(segment => currentTimeMs >= segment.start_ms && currentTimeMs < segment.end_ms);
  const showChinese = data?.subtitle_mode === 'bilingual' || data?.subtitle_mode === 'zh';
  const showEnglish = data?.subtitle_mode === 'bilingual' || data?.subtitle_mode === 'en';

  return <main className="shared-page-shell">
    <header className="shared-page-header">
      <div><span className="hero-kicker">A QUOTE CAN TAKE YOU BACK</span><h1>分享的片段</h1></div>
      <a className="outline-button" href="/">返回到主页</a>
    </header>
    {loading && <div className="notice-card" role="status"><span className="notice-icon">…</span><div><h2>正在验证分享链接</h2><p>服务端会重新检查链接状态和素材分享权限。</p></div></div>}
    {error && !loading && <div className="notice-card error-notice" role="alert"><span className="notice-icon">!</span><div><h2>无法打开分享内容</h2><p>{error}</p></div></div>}
    {data && !error && <article className="shared-clip-card">
      <div className="shared-clip-heading">
        <div><span className="section-kicker">{data.series_title_en} · S{String(data.season).padStart(2, '0')}E{String(data.episode).padStart(2, '0')}</span>
          <h2>{data.episode_title || '分享片段'}</h2></div>
        <span className="shared-time-range">{formatTime(data.start_ms)} — {formatTime(data.end_ms)}</span>
      </div>
      <div className="shared-player-wrap">
        <video key={`${data.version_id}:${mediaRevision}`} ref={videoRef} className="shared-video" crossOrigin="anonymous"
          src={data.media_url} controls playsInline preload="metadata"
          onLoadedMetadata={event => {
            const video = event.currentTarget;
            const resume = resumeTimeRef.current ?? data.start_ms / 1000;
            video.currentTime = Math.max(data.start_ms / 1000, Math.min(resume, data.end_ms / 1000));
            resumeTimeRef.current = null;
          }}
          onTimeUpdate={event => constrainPlayback(event.currentTarget)}
          onError={() => { void refreshMediaAddress(); }} />
        {activeCaption && data.subtitle_mode !== 'off' && <div className="video-caption shared-video-caption" aria-live="polite">
          {showChinese && activeCaption.text_zh && <span className="video-caption-zh">{activeCaption.text_zh}</span>}
          {showEnglish && activeCaption.text_en && <span className="video-caption-en">{activeCaption.text_en}</span>}
        </div>}
      </div>
      <div className="shared-dialogue-list">
        {data.segments.map(segment => <div key={segment.id}>
          {data.subtitle_mode !== 'off' && (showChinese && segment.text_zh || showEnglish && segment.text_en) && <span className="shared-dialogue-time">{formatTime(segment.start_ms)}</span>}
          {showChinese && segment.text_zh && <p className="modal-line modal-line-zh">{segment.text_zh}</p>}
          {showEnglish && segment.text_en && <p className="modal-line modal-line-en">{segment.text_en}</p>}
        </div>)}
      </div>
      <p className="shared-page-note">分享链接会在 {data.media_url_expires_in_seconds / 60} 分钟后重新签发播放地址；片段最多播放 60 秒。</p>
    </article>}
    <footer className="app-footer">由 Dialogue Library 本地应用提供 · SEARCH · LISTEN · REMEMBER</footer>
  </main>;
}
