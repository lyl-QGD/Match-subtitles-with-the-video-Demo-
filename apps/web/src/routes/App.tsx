import { FormEvent, useEffect, useRef, useState } from 'react';
import { ApiError, CreatedShare, DialogueClip, PlaybackSegment, SearchResponse, SearchResult, SegmentPlayback, createShare, featuredDialogue, revokeManagedShare, searchDialogue, segmentPlayback } from '../lib/api';

type SearchState = { query: string; season?: number; episode?: number; page: number };
type ModalState = { clips: DialogueClip[]; index: number };
type ActiveShare = CreatedShare & { url: string; status: 'active' | 'revoked'; copied: boolean };

function stateFromUrl(): SearchState {
  const params = new URLSearchParams(window.location.search);
  const season = Number(params.get('season'));
  const episode = Number(params.get('episode'));
  const page = Number(params.get('page'));
  return {
    query: params.get('q') ?? '',
    season: Number.isInteger(season) && season > 0 && season <= 100 ? season : undefined,
    episode: Number.isInteger(episode) && episode > 0 && episode <= 100 ? episode : undefined,
    page: Number.isInteger(page) && page > 0 && page <= 10000 ? page : 1,
  };
}

function writeStateToUrl(state: SearchState) {
  const params = new URLSearchParams();
  if (state.query) params.set('q', state.query);
  if (state.season !== undefined) params.set('season', String(state.season));
  if (state.episode !== undefined) params.set('episode', String(state.episode));
  if (state.page > 1) params.set('page', String(state.page));
  const query = params.toString();
  window.history.pushState(null, '', `${window.location.pathname}${query ? `?${query}` : ''}`);
}

function normalizedWithMap(value: string) {
  let normalized = '';
  const starts: number[] = [];
  const ends: number[] = [];
  for (let index = 0; index < value.length;) {
    const original = String.fromCodePoint(value.codePointAt(index)!);
    const end = index + original.length;
    for (const character of original.normalize('NFKC').toLocaleLowerCase()) {
      if (/\s/u.test(character)) {
        if (!normalized || normalized.endsWith(' ')) {
          if (normalized.endsWith(' ')) ends[ends.length - 1] = end;
          continue;
        }
        normalized += ' ';
        starts.push(index);
        ends.push(end);
      } else {
        for (let unit = 0; unit < character.length; unit++) {
          normalized += character[unit];
          starts.push(index);
          ends.push(end);
        }
      }
    }
    index = end;
  }
  if (normalized.endsWith(' ')) {
    normalized = normalized.slice(0, -1);
    starts.pop();
    ends.pop();
  }
  return { normalized, starts, ends };
}

function highlightParts(text: string, query: string) {
  const source = normalizedWithMap(text);
  const needle = query.normalize('NFKC').toLocaleLowerCase().trim().replace(/\s+/gu, ' ');
  if (!needle) return [{ text, matched: false }];
  const ranges: { start: number; end: number }[] = [];
  let cursor = 0;
  while (cursor < source.normalized.length) {
    const match = source.normalized.indexOf(needle, cursor);
    if (match < 0) break;
    const start = source.starts[match];
    const end = source.ends[match + needle.length - 1];
    const previous = ranges[ranges.length - 1];
    if (previous && start <= previous.end) previous.end = Math.max(previous.end, end);
    else ranges.push({ start, end });
    cursor = match + Math.max(needle.length, 1);
  }
  if (!ranges.length) return [{ text, matched: false }];
  const parts: { text: string; matched: boolean }[] = [];
  let position = 0;
  for (const range of ranges) {
    if (range.start > position) parts.push({ text: text.slice(position, range.start), matched: false });
    parts.push({ text: text.slice(range.start, range.end), matched: true });
    position = range.end;
  }
  if (position < text.length) parts.push({ text: text.slice(position), matched: false });
  return parts;
}

function Highlight({ text, query }: { text: string; query: string }) {
  return <>{highlightParts(text, query).map((part, index) => part.matched
    ? <mark key={index}>{part.text}</mark>
    : <span key={index}>{part.text}</span>)}</>;
}

function formatTime(ms: number) {
  const minutes = Math.floor(ms / 60000);
  const seconds = Math.floor((ms % 60000) / 1000);
  const millis = ms % 1000;
  return `${minutes}:${String(seconds).padStart(2, '0')}.${String(millis).padStart(3, '0')}`;
}

function ClipFrame({ clip, label }: { clip: DialogueClip; label: string }) {
  const frameRef = useRef<HTMLSpanElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [inView, setInView] = useState(false);
  const [mediaUrl, setMediaUrl] = useState('');
  const [frameReady, setFrameReady] = useState(false);
  const [frameFailed, setFrameFailed] = useState(false);
  const frameTimeMs = clip.start_ms + Math.max(0, (clip.end_ms - clip.start_ms) / 2);

  useEffect(() => {
    const node = frameRef.current;
    if (!node) return;
    if (!('IntersectionObserver' in window)) {
      setInView(true);
      return;
    }
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) {
        setInView(true);
        observer.disconnect();
      }
    }, { rootMargin: '180px' });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!inView) return;
    const controller = new AbortController();
    let active = true;
    segmentPlayback(clip.id, controller.signal)
      .then(playback => { if (active) setMediaUrl(playback.media_url); })
      .catch(error => {
        if (active && !(error instanceof DOMException && error.name === 'AbortError')) setFrameFailed(true);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [clip.id, inView]);

  function representativeTime(video: HTMLVideoElement) {
    const targetSeconds = frameTimeMs / 1000;
    return Number.isFinite(video.duration) ? Math.min(targetSeconds, Math.max(0, video.duration - 0.08)) : targetSeconds;
  }

  function seekToRepresentativeFrame() {
    const video = videoRef.current;
    if (!video || video.readyState < HTMLMediaElement.HAVE_METADATA) return;
    video.pause();
    const safeTarget = representativeTime(video);
    if (Math.abs(video.currentTime - safeTarget) >= 0.03) video.currentTime = safeTarget;
  }

  return <span ref={frameRef} className={`clip-frame${frameReady ? ' frame-ready' : ''}`}>
    {mediaUrl && <video
      ref={videoRef}
      className="clip-frame-video"
      crossOrigin="anonymous"
      src={mediaUrl}
      preload="metadata"
      muted
      playsInline
      tabIndex={-1}
      aria-hidden="true"
      onLoadedMetadata={seekToRepresentativeFrame}
      onLoadedData={() => {
        const video = videoRef.current;
        if (video && Math.abs(video.currentTime - representativeTime(video)) < 0.08) setFrameReady(true);
      }}
      onSeeked={() => { videoRef.current?.pause(); setFrameReady(true); }}
      onError={() => setFrameFailed(true)}
    />}
    <span className="clip-frame-scrim" aria-hidden="true" />
    <span className="clip-frame-top"><span>S{String(clip.season).padStart(2, '0')} · E{String(clip.episode).padStart(2, '0')}</span><span>{label}</span></span>
    <span className="clip-frame-caption">
      <span className="clip-frame-en">{clip.text_en}</span>
      <span className="clip-frame-zh">{clip.text_zh}</span>
    </span>
    <span className="clip-frame-bottom"><span>{formatTime(clip.start_ms)}</span><span className="play-glyph" aria-hidden="true">▶</span></span>
    {inView && !frameReady && <span className="clip-frame-status" aria-hidden="true">{frameFailed ? '画面预览暂不可用' : '正在加载画面…'}</span>}
  </span>;
}

function ClipCard({ clip, index, label, query, showFrame = false, onOpen }: {
  clip: DialogueClip;
  index: number;
  label: string;
  query?: string;
  showFrame?: boolean;
  onOpen: () => void;
}) {
  return <button type="button" className={`clip-card tone-${index % 6}${showFrame ? ' screenshot-card' : ''}`} onClick={onOpen}
    aria-label={`打开第 ${clip.season} 季第 ${clip.episode} 集 ${formatTime(clip.start_ms)} 的台词片段：${clip.text_zh} / ${clip.text_en}`}>
    {showFrame ? <ClipFrame clip={clip} label={label} /> : <>
      <span className="clip-card-top"><span>{label}</span><span>S{String(clip.season).padStart(2, '0')} · E{String(clip.episode).padStart(2, '0')}</span></span>
      <span className="clip-quote-mark" aria-hidden="true">“</span>
      <span className="clip-card-zh">{query ? <Highlight text={clip.text_zh} query={query} /> : clip.text_zh}</span>
      <span className="clip-card-en">{query ? <Highlight text={clip.text_en} query={query} /> : clip.text_en}</span>
      <span className="clip-card-bottom"><span>{formatTime(clip.start_ms)}</span><span className="play-glyph" aria-hidden="true">▶</span></span>
    </>}
  </button>;
}

function ClipModal({ state, onClose, onMove }: {
  state: ModalState;
  onClose: () => void;
  onMove: (index: number) => void;
}) {
  const clip = state.clips[state.index];
  const videoRef = useRef<HTMLVideoElement>(null);
  const clipIdRef = useRef(clip.id);
  const mountedRef = useRef(true);
  clipIdRef.current = clip.id;
  const resumeTimeRef = useRef<number | null>(null);
  const renewalInProgressRef = useRef(false);
  const renewalCountRef = useRef(0);
  const [playback, setPlayback] = useState<SegmentPlayback | null>(null);
  const [segments, setSegments] = useState<PlaybackSegment[]>([]);
  const [range, setRange] = useState<{ startIndex: number; endIndex: number } | null>(null);
  const [startMs, setStartMs] = useState(clip.start_ms);
  const [endMs, setEndMs] = useState(clip.end_ms);
  const [durationMs, setDurationMs] = useState<number | null>(null);
  const [currentTimeMs, setCurrentTimeMs] = useState(clip.start_ms);
  const [subtitleMode, setSubtitleMode] = useState<'both' | 'zh' | 'en'>('both');
  const [mediaRevision, setMediaRevision] = useState(0);
  const [screenshot, setScreenshot] = useState<{ url: string; clipId: string; timeMs: number; width: number; height: number } | null>(null);
  const [screenshotError, setScreenshotError] = useState('');
  const [share, setShare] = useState<ActiveShare | null>(null);
  const [shareLoading, setShareLoading] = useState(false);
  const [shareError, setShareError] = useState('');
  const shareLinkRef = useRef<HTMLInputElement>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectionMessage, setSelectionMessage] = useState('');

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    if (!screenshot?.url) return;
    const objectUrl = screenshot.url;
    return () => URL.revokeObjectURL(objectUrl);
  }, [screenshot?.url]);

  useEffect(() => {
    let current = true;
    setPlayback(null);
    setSegments([]);
    setRange(null);
    setError('');
    setSelectionMessage('');
    setScreenshot(null);
    setScreenshotError('');
    setShare(null);
    setShareError('');
    setSubtitleMode('both');
    setDurationMs(null);
    setStartMs(clip.start_ms);
    setEndMs(clip.end_ms);
    setCurrentTimeMs(clip.start_ms);
    resumeTimeRef.current = null;
    renewalCountRef.current = 0;
    setLoading(true);
    segmentPlayback(clip.id).then(result => {
      if (!current) return;
      const sameRevision = result.segments.filter(segment =>
        segment.version_id === result.version_id && segment.revision_id === result.revision_id);
      const anchorIndex = sameRevision.findIndex(segment => segment.id === clip.id);
      if (anchorIndex < 0) {
        setError('当前台词不在活动字幕版本中，请重新打开结果。');
        return;
      }
      const anchor = sameRevision[anchorIndex];
      setPlayback(result);
      setSegments(sameRevision);
      setRange({ startIndex: anchorIndex, endIndex: anchorIndex });
      setStartMs(anchor.start_ms);
      setEndMs(Math.min(anchor.end_ms, anchor.start_ms + 60_000));
      setCurrentTimeMs(anchor.start_ms);
    }).catch(cause => {
      if (current) setError(cause instanceof Error ? cause.message : '视频暂时无法加载。');
    }).finally(() => {
      if (current) setLoading(false);
    });
    return () => { current = false; };
  }, [clip.id]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
      if (event.key === 'ArrowLeft' && state.index > 0) onMove(state.index - 1);
      if (event.key === 'ArrowRight' && state.index < state.clips.length - 1) onMove(state.index + 1);
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose, onMove, state.index, state.clips.length]);

  const selectedStart = startMs;
  const selectedEnd = endMs;
  const activeCaption = segments.find(segment => currentTimeMs >= segment.start_ms && currentTimeMs < segment.end_ms);
  const effectiveDuration = durationMs ?? playback?.duration_ms ?? null;

  async function playClip() {
    const video = videoRef.current;
    if (!video || !playback || selectedEnd <= selectedStart) return;
    video.muted = false;
    video.volume = 1;
    const start = selectedStart / 1000;
    const end = selectedEnd / 1000;
    if (video.currentTime < start || video.currentTime >= end) video.currentTime = start;
    try { await video.play(); } catch { setError('浏览器未能开始播放，请使用视频控件重试。'); }
  }

  function keepPlaybackInClip(video: HTMLVideoElement) {
    if (!playback) return;
    const start = selectedStart / 1000;
    const end = selectedEnd / 1000;
    setCurrentTimeMs(Math.floor(video.currentTime * 1000));
    if (video.currentTime < start - 0.25) video.currentTime = start;
    if (video.currentTime >= end) {
      video.pause();
      video.currentTime = end;
      setCurrentTimeMs(selectedEnd);
    }
  }

  function validateRange(startIndex: number, endIndex: number): string | null {
    if (startIndex > endIndex) return '起点必须位于终点之前。';
    const first = segments[startIndex];
    const last = segments[endIndex];
    if (!first || !last || !playback
      || first.version_id !== playback.version_id || last.version_id !== playback.version_id
      || first.revision_id !== playback.revision_id || last.revision_id !== playback.revision_id) {
      return '只能选择同一视频版本和同一字幕修订中的台词。';
    }
    for (let index = startIndex + 1; index <= endIndex; index += 1) {
      if (segments[index].sequence !== segments[index - 1].sequence + 1) {
        return '所选台词之间存在未审核的字幕空档，请缩小选择范围。';
      }
    }
    const start = first.start_ms;
    const end = last.end_ms;
    if (end <= start) return '结束时间必须晚于开始时间。';
    if (end - start > 60_000) return '素材片段最长为 60 秒，请缩小选择范围。';
    if (effectiveDuration !== null && end > effectiveDuration) return '选择范围超出视频时长。';
    return null;
  }

  function setBoundary(index: number, edge: 'start' | 'end') {
    if (!range) return;
    const next = edge === 'start'
      ? { startIndex: index, endIndex: range.endIndex }
      : { startIndex: range.startIndex, endIndex: index };
    const problem = validateRange(next.startIndex, next.endIndex);
    if (problem) {
      setSelectionMessage(problem);
      return;
    }
    setRange(next);
    setStartMs(segments[next.startIndex].start_ms);
    setEndMs(segments[next.endIndex].end_ms);
    setSelectionMessage('');
    const video = videoRef.current;
    if (video) {
      video.pause();
      video.currentTime = segments[next.startIndex].start_ms / 1000;
      setCurrentTimeMs(segments[next.startIndex].start_ms);
    }
  }

  function adjustBoundary(edge: 'start' | 'end', delta: number) {
    const nextStart = edge === 'start' ? startMs + delta : startMs;
    const nextEnd = edge === 'end' ? endMs + delta : endMs;
    if (nextStart < 0 || nextEnd <= nextStart || (effectiveDuration !== null && nextEnd > effectiveDuration)) {
      setSelectionMessage('时间需要在视频范围内，且结束时间必须晚于开始时间。');
      return;
    }
    if (nextEnd - nextStart > 60_000) {
      setSelectionMessage('素材片段最长为 60 秒，请缩小选择范围。');
      return;
    }
    setStartMs(nextStart);
    setEndMs(nextEnd);
    setSelectionMessage('');
    const video = videoRef.current;
    if (video && (video.currentTime * 1000 < nextStart || video.currentTime * 1000 >= nextEnd)) {
      video.pause();
      video.currentTime = nextStart / 1000;
      setCurrentTimeMs(nextStart);
    }
  }

  function resetSelection() {
    const index = segments.findIndex(segment => segment.id === clip.id
      && segment.version_id === playback?.version_id
      && segment.revision_id === playback?.revision_id);
    if (index < 0 || !playback) return;
    const anchor = segments[index];
    const resetEnd = Math.min(anchor.end_ms, anchor.start_ms + 60_000, effectiveDuration ?? Number.POSITIVE_INFINITY);
    setRange({ startIndex: index, endIndex: index });
    setStartMs(anchor.start_ms);
    setEndMs(resetEnd);
    setSelectionMessage('');
    const video = videoRef.current;
    if (video) {
      video.pause();
      video.currentTime = anchor.start_ms / 1000;
      setCurrentTimeMs(anchor.start_ms);
    }
  }

  function captureFrame() {
    setScreenshotError('');
    const video = videoRef.current;
    if (!video || video.readyState < 2 || !video.videoWidth || !video.videoHeight) {
      setScreenshotError('视频画面还没准备好，请先播放或等待画面加载后再截图。');
      return;
    }

    const canvas = document.createElement('canvas');
    const width = video.videoWidth;
    const height = video.videoHeight;
    const capturedTimeMs = Math.floor(video.currentTime * 1000);
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext('2d');
    if (!context) {
      setScreenshotError('当前浏览器无法生成截图，请重试。');
      return;
    }

    try {
      context.drawImage(video, 0, 0, width, height);
      canvas.toBlob(blob => {
        canvas.width = 0;
        canvas.height = 0;
        if (!mountedRef.current || clipIdRef.current !== clip.id) return;
        if (!blob) {
          setScreenshotError('截图生成失败，请重试。');
          return;
        }
        const url = URL.createObjectURL(blob);
        setScreenshot({ url, clipId: clip.id, timeMs: capturedTimeMs, width, height });
        setScreenshotError('');
      }, 'image/jpeg', 0.92);
    } catch {
      canvas.width = 0;
      canvas.height = 0;
      setScreenshotError('浏览器未允许读取视频画面，请检查 R2 CORS 配置后重试。');
    }
  }

  async function createShareLink() {
    if (!playback || !range || !playback.allow_share) return;
    const problem = validateRange(range.startIndex, range.endIndex);
    if (problem || selectedEnd <= selectedStart || selectedEnd - selectedStart > 60_000) {
      setShareError(problem || '请先选择有效的片段范围。');
      return;
    }
    setShareLoading(true);
    setShareError('');
    try {
      const created = await createShare({
        version_id: playback.version_id,
        revision_id: playback.revision_id,
        segment_ids: segments.slice(range.startIndex, range.endIndex + 1).map(segment => segment.id),
        start_ms: selectedStart,
        end_ms: selectedEnd,
        subtitle_mode: subtitleMode === 'both' ? 'bilingual' : subtitleMode,
      });
      const url = new URL(`/s/${created.token}`, window.location.origin).toString();
      setShare({ ...created, url, status: 'active', copied: false });
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 'sharing_not_authorized') {
        setShareError('当前素材未开启分享授权，暂时不能创建分享链接。');
      } else {
        setShareError(cause instanceof Error ? cause.message : '创建分享链接失败，请重试。');
      }
    } finally {
      setShareLoading(false);
    }
  }

  async function copyShareLink() {
    if (!share || share.status !== 'active') return;
    try {
      await navigator.clipboard.writeText(share.url);
      setShare(current => current ? { ...current, copied: true } : null);
      setShareError('');
    } catch {
      shareLinkRef.current?.focus();
      shareLinkRef.current?.select();
      setShareError('链接已选中，请按 Ctrl+C 复制。');
    }
  }

  async function revokeShare() {
    if (!share || share.status !== 'active' || !window.confirm('撤销后，这个分享链接会立即失效。确定撤销吗？')) return;
    setShareLoading(true);
    setShareError('');
    try {
      await revokeManagedShare(share.share_id);
      setShare(current => current ? { ...current, status: 'revoked' } : null);
    } catch (cause) {
      setShareError(cause instanceof Error ? cause.message : '撤销分享失败，请重试。');
    } finally {
      setShareLoading(false);
    }
  }

  async function renewPlayback() {
    const video = videoRef.current;
    if (!playback || renewalInProgressRef.current) return;
    if (renewalCountRef.current >= 1) {
      setError('视频地址刷新后仍无法加载。请检查浏览器网络和 R2 CORS 配置，再重试。');
      return;
    }
    renewalInProgressRef.current = true;
    renewalCountRef.current += 1;
    resumeTimeRef.current = video?.currentTime ?? selectedStart / 1000;
    setError('视频连接中断，正在刷新播放地址…');
    try {
      const refreshed = await segmentPlayback(clip.id);
      if (refreshed.version_id !== playback.version_id || refreshed.revision_id !== playback.revision_id) {
        setError('素材版本已更新。为避免字幕与视频错配，请关闭弹层并重新打开结果。');
        return;
      }
      setPlayback(refreshed);
      setMediaRevision(value => value + 1);
      setError('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '播放地址刷新失败，请稍后重试。');
    } finally {
      renewalInProgressRef.current = false;
    }
  }

  const subtitle = activeCaption;
  const currentScreenshot = screenshot?.clipId === clip.id ? screenshot : null;

  return <div className="modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="clip-modal" role="dialog" aria-modal="true" aria-labelledby="clip-modal-title">
      <div className="modal-topbar">
        <div><span className="modal-eyebrow">THE BIG BANG THEORY · S{String(clip.season).padStart(2, '0')}E{String(clip.episode).padStart(2, '0')}</span>
          <h2 id="clip-modal-title">{clip.episode_title || '经典台词'}</h2></div>
        <button type="button" className="icon-button" aria-label="关闭" onClick={onClose}>×</button>
      </div>
      <div className={`modal-media-layout${currentScreenshot ? ' has-screenshot' : ''}`}>
        <div className="modal-media-main">
          <div className="modal-player-wrap">
            {playback && <>
              <video key={`${clip.id}:${mediaRevision}`} ref={videoRef} className="modal-video" crossOrigin="anonymous" src={playback.media_url} controls playsInline preload="metadata"
                onLoadedMetadata={event => {
                  const video = event.currentTarget;
                  setDurationMs(Number.isFinite(video.duration) ? Math.floor(video.duration * 1000) : null);
                  const requested = resumeTimeRef.current ?? selectedStart / 1000;
                  video.currentTime = Math.max(selectedStart / 1000, Math.min(requested, selectedEnd / 1000));
                  resumeTimeRef.current = null;
                }}
                onTimeUpdate={event => keepPlaybackInClip(event.currentTarget)}
                onError={() => { void renewPlayback(); }} />
              {subtitle && <div className="video-caption" aria-live="polite">
                {(subtitleMode === 'both' || subtitleMode === 'zh') && subtitle.text_zh && <span className="video-caption-zh">{subtitle.text_zh}</span>}
                {(subtitleMode === 'both' || subtitleMode === 'en') && subtitle.text_en && <span className="video-caption-en">{subtitle.text_en}</span>}
              </div>}
            </>}
            {loading && <div className="player-message">正在准备视频片段…</div>}
            {error && <div className="player-message player-error">{error}{!loading && <button type="button" className="caption-retry" onClick={() => { renewalCountRef.current = 0; void renewPlayback(); }}>重试播放</button>}</div>}
          </div>
          <div className="screenshot-tools">
            <button type="button" className="screenshot-button" disabled={!playback || loading} onClick={captureFrame}>▣ 截取当前画面</button>
            <span>截图仅在本页临时预览；下载后保存到本地，不上传云端</span>
          </div>
          {screenshotError && <p className="screenshot-error" role="alert">{screenshotError}</p>}
        </div>
        {currentScreenshot && <aside className="screenshot-preview" aria-label="截图临时预览">
          <div className="screenshot-preview-heading">
            <div><strong>截图预览</strong><span>{formatTime(currentScreenshot.timeMs)}</span></div>
            <button type="button" className="icon-button screenshot-close" aria-label="关闭截图预览" onClick={() => setScreenshot(null)}>×</button>
          </div>
          <img src={currentScreenshot.url} alt={`视频 ${formatTime(currentScreenshot.timeMs)} 的临时截图`} />
          <div className="screenshot-download-row">
            <a className="screenshot-download" href={currentScreenshot.url}
              download={`${clip.series_slug}-s${String(clip.season).padStart(2, '0')}-e${String(clip.episode).padStart(2, '0')}-${currentScreenshot.timeMs}ms.jpg`}>
              ↓ 下载到本地
            </a>
            <span>保存到浏览器默认下载位置</span>
          </div>
          <p>{currentScreenshot.width} × {currentScreenshot.height} 像素 · 关闭弹层后自动清除</p>
        </aside>}
      </div>
      <div className="subtitle-toolbar" role="group" aria-label="字幕显示方式">
        <span>字幕</span>
        <button type="button" className={subtitleMode === 'both' ? 'active' : ''} onClick={() => setSubtitleMode('both')}>双语</button>
        <button type="button" className={subtitleMode === 'zh' ? 'active' : ''} onClick={() => setSubtitleMode('zh')}>中文</button>
        <button type="button" className={subtitleMode === 'en' ? 'active' : ''} onClick={() => setSubtitleMode('en')}>English</button>
      </div>
      <div className="selection-panel">
        <div className="selection-heading">
          <div><strong>选择连续台词</strong><span>起止范围内最多 60 秒；本次选择只在当前弹层中生效。</span></div>
          <span className="selection-duration">{formatTime(Math.max(0, selectedEnd - selectedStart))} / 1:00</span>
        </div>
        <div className="selection-boundaries">
          <div><span>开始</span><strong>{formatTime(selectedStart)}</strong>
            <button type="button" className="nudge-button" aria-label="开始时间提前 100 毫秒" onClick={() => adjustBoundary('start', -100)}>−</button>
            <button type="button" className="nudge-button" aria-label="开始时间延后 100 毫秒" onClick={() => adjustBoundary('start', 100)}>+</button></div>
          <div><span>结束</span><strong>{formatTime(selectedEnd)}</strong>
            <button type="button" className="nudge-button" aria-label="结束时间提前 100 毫秒" onClick={() => adjustBoundary('end', -100)}>−</button>
            <button type="button" className="nudge-button" aria-label="结束时间延后 100 毫秒" onClick={() => adjustBoundary('end', 100)}>+</button></div>
        </div>
        {selectionMessage && <p className="selection-warning" role="status">{selectionMessage}</p>}
        <div className="transcript-toolbar">
          <span>同一视频与字幕版本中的台词</span>
          <button type="button" className="selection-reset" disabled={!playback || segments.length === 0} onClick={resetSelection}>↺ 重置选择</button>
        </div>
        <div className="transcript-list" aria-label="同一字幕版本中的连续台词">
          {segments.map((segment, index) => {
            const inRange = Boolean(range && index >= range.startIndex && index <= range.endIndex);
            const edge = range?.startIndex === index ? ' 起点' : range?.endIndex === index ? ' 终点' : '';
            return <div className={`transcript-row${inRange ? ' transcript-selected' : ''}`} key={segment.id}>
              <button type="button" className="transcript-seek" onClick={() => {
                const video = videoRef.current;
                if (video) { video.pause(); video.currentTime = segment.start_ms / 1000; setCurrentTimeMs(segment.start_ms); }
              }} aria-label={`定位到 ${formatTime(segment.start_ms)} ${segment.text_zh || segment.text_en}`}>
                <span className="transcript-time">{formatTime(segment.start_ms)}{edge}</span>
                <span className="transcript-zh">{segment.text_zh || '（无中文字幕）'}</span>
                <span className="transcript-en">{segment.text_en || '(No English subtitle)'}</span>
              </button>
              <div className="transcript-actions">
                <button type="button" onClick={() => setBoundary(index, 'start')} aria-label={`将 ${formatTime(segment.start_ms)} 设为开始`}>起点</button>
                <button type="button" onClick={() => setBoundary(index, 'end')} aria-label={`将 ${formatTime(segment.end_ms)} 设为结束`}>终点</button>
              </div>
            </div>;
          })}
          {!loading && segments.length === 0 && <p className="section-message">当前字幕版本没有可选择的相邻台词。</p>}
        </div>
      </div>
      <div className="modal-actions">
        <button type="button" className="outline-button" disabled={state.index === 0} onClick={() => onMove(state.index - 1)}>← 上一段</button>
        <button type="button" className="modal-play-button" disabled={!playback || loading || selectedEnd <= selectedStart} onClick={() => void playClip()}>{loading ? '准备中…' : '播放所选范围'}</button>
        <button type="button" className="outline-button" disabled={state.index >= state.clips.length - 1} onClick={() => onMove(state.index + 1)}>下一段 →</button>
      </div>
      <section className="share-create-panel" aria-labelledby="share-create-title">
        <div className="share-create-heading">
          <div><strong id="share-create-title">分享这个片段</strong><span>创建后 7 天有效；链接打开时会重新检查分享权限。</span></div>
          <button type="button" className="share-create-button" disabled={!playback?.allow_share || !range || loading || shareLoading || share?.status === 'active'} onClick={() => void createShareLink()}>
            {shareLoading ? '处理中…' : share?.status === 'active' ? '已创建分享' : '创建分享链接'}
          </button>
        </div>
        {playback && !playback.allow_share && <p className="share-permission-note">该素材目前只允许预览，尚未开启分享权限。</p>}
        {shareError && <p className="share-error" role="status">{shareError}</p>}
        {share && <div className={`share-created${share.status === 'revoked' ? ' share-revoked' : ''}`}>
          <p>{share.status === 'revoked' ? '此分享链接已撤销。' : `链接有效至 ${new Date(share.expires_at).toLocaleString()}`}</p>
          <div className="share-link-row">
            <input ref={shareLinkRef} aria-label="分享链接" value={share.url} readOnly onFocus={event => event.currentTarget.select()} />
            <button type="button" className="outline-button" disabled={share.status !== 'active'} onClick={() => void copyShareLink()}>{share.copied ? '已复制' : '复制链接'}</button>
          </div>
          <div className="share-created-actions">
            <span>本机链接仅能在此电脑打开；部署应用后才可分享给外网用户。</span>
            {share.status === 'active' && <button type="button" className="share-revoke-button" disabled={shareLoading} onClick={() => void revokeShare()}>撤销链接</button>}
          </div>
        </div>}
      </section>
      {effectiveDuration !== null && <p className="modal-timestamp">视频时长 {formatTime(effectiveDuration)} · 选择校验边界 0 至视频结尾</p>}
      <div className="modal-counter">{state.index + 1} / {state.clips.length} · 可用键盘 ← → 切换</div>
    </section>
  </div>;
}

export function App() {
  const [active, setActive] = useState<SearchState>(stateFromUrl);
  const [keyword, setKeyword] = useState(active.query);
  const [seasonInput, setSeasonInput] = useState(active.season ? String(active.season) : '');
  const [episodeInput, setEpisodeInput] = useState(active.episode ? String(active.episode) : '');
  const [data, setData] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  const [retry, setRetry] = useState(0);
  const [validation, setValidation] = useState('');
  const [featured, setFeatured] = useState<DialogueClip[]>([]);
  const [featuredError, setFeaturedError] = useState('');
  const [featuredLoading, setFeaturedLoading] = useState(true);
  const [refreshFeatured, setRefreshFeatured] = useState(0);
  const [modal, setModal] = useState<ModalState | null>(null);

  useEffect(() => {
    const syncFromUrl = () => {
      const next = stateFromUrl();
      setActive(next);
      setKeyword(next.query);
      setSeasonInput(next.season ? String(next.season) : '');
      setEpisodeInput(next.episode ? String(next.episode) : '');
    };
    window.addEventListener('popstate', syncFromUrl);
    return () => window.removeEventListener('popstate', syncFromUrl);
  }, []);

  useEffect(() => {
    let current = true;
    setFeaturedLoading(true);
    setFeaturedError('');
    featuredDialogue().then(clips => {
      if (current) setFeatured(clips);
    }).catch(cause => {
      if (current) setFeaturedError(cause instanceof Error ? cause.message : '精选片段暂时无法加载。');
    }).finally(() => {
      if (current) setFeaturedLoading(false);
    });
    return () => { current = false; };
  }, [refreshFeatured]);

  useEffect(() => {
    if (!active.query.trim()) {
      setData(null);
      setError(null);
      setLoading(false);
      return;
    }
    let current = true;
    setLoading(true);
    setError(null);
    searchDialogue(active).then(response => {
      if (current) setData(response);
    }).catch(cause => {
      if (current) setError(cause instanceof Error ? cause : new Error('搜索暂时不可用。'));
    }).finally(() => {
      if (current) setLoading(false);
    });
    return () => { current = false; };
  }, [active, retry]);

  function runSearch(queryValue: string, seasonValue = seasonInput, episodeValue = episodeInput) {
    const query = queryValue.trim();
    if (!query) {
      setValidation('请输入一个中文或英文关键词。');
      return;
    }
    const season = seasonValue ? Number(seasonValue) : undefined;
    const episode = episodeValue ? Number(episodeValue) : undefined;
    if ((season !== undefined && (!Number.isInteger(season) || season < 1 || season > 100))
      || (episode !== undefined && (!Number.isInteger(episode) || episode < 1 || episode > 100))) {
      setValidation('季和集请输入 1 到 100 之间的整数，或留空搜索全部。');
      return;
    }
    setValidation('');
    const next: SearchState = { query, season, episode, page: 1 };
    writeStateToUrl(next);
    setActive(next);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    runSearch(keyword);
  }

  function goToPage(page: number) {
    const next = { ...active, page };
    writeStateToUrl(next);
    setActive(next);
  }

  function closeSearch() {
    const next: SearchState = { query: '', page: 1 };
    writeStateToUrl(next);
    setActive(next);
    setKeyword('');
    setSeasonInput('');
    setEpisodeInput('');
    setData(null);
    setError(null);
    setValidation('');
  }

  function openClip(clips: DialogueClip[], index: number) {
    setModal({ clips, index });
  }

  function moveModal(index: number) {
    setModal(current => current ? { ...current, index: Math.max(0, Math.min(index, current.clips.length - 1)) } : null);
  }

  const unavailable = error instanceof ApiError && error.code === 'database_unavailable';
  const visibleData = data && data.query === active.query && data.page === active.page
    && data.season === (active.season ?? null) && data.episode === (active.episode ?? null)
    ? data : null;

  return <main className="app-shell">
    <header className="hero">
      <span className="hero-kicker">A QUOTE CAN TAKE YOU BACK</span>
      <h1>The Big Bang Theory</h1>
      <p className="hero-quote">“So if a photon is directed through a plane…”</p>
      <p className="hero-quote-zh">如果一个光子打向有两个狭缝的平面</p>
      <form className="search-form" onSubmit={submit}>
        <label className="sr-only" htmlFor="dialogue-query">搜索台词</label>
        <div className="query-row">
          <span className="search-icon" aria-hidden="true">⌕</span>
          <input id="dialogue-query" value={keyword} onChange={event => setKeyword(event.target.value)}
            maxLength={120} placeholder="输入中文或英文台词" autoComplete="off" />
          <button type="submit" disabled={loading}>{loading ? '搜索中…' : '搜索台词'}</button>
        </div>
        <div className="search-examples">
          <span>试试这些关键词</span>
          <button type="button" className="keyword-chip" onClick={() => { setKeyword('photon'); runSearch('photon'); }}>photon</button>
          <button type="button" className="keyword-chip" onClick={() => { setKeyword('光子打向'); runSearch('光子打向'); }}>光子打向</button>
        </div>
        <div className="filters-row">
          <label>季
            <input type="number" min="1" max="100" value={seasonInput} onChange={event => setSeasonInput(event.target.value)} placeholder="全部" />
          </label>
          <label>集
            <input type="number" min="1" max="100" value={episodeInput} onChange={event => setEpisodeInput(event.target.value)} placeholder="全部" />
          </label>
          <span className="filter-help">季、集留空时搜索全部已发布素材</span>
        </div>
        {validation && <p className="form-error" role="alert">{validation}</p>}
      </form>
    </header>

    <section className="featured-section" aria-labelledby="featured-heading">
      <div className="section-heading">
        <div><span className="section-kicker">PICK A MOMENT</span><h2 id="featured-heading">随机切片</h2></div>
        <button type="button" className="text-button" onClick={() => setRefreshFeatured(value => value + 1)}>换一批 <span aria-hidden="true">↗</span></button>
      </div>
      {featuredLoading && <p className="section-message">正在挑选台词片段…</p>}
      {featuredError && <div className="section-message section-error" role="alert"><span>{featuredError}</span><button type="button" className="text-button" onClick={() => setRefreshFeatured(value => value + 1)}>重试</button></div>}
      {!featuredLoading && !featuredError && featured.length === 0 && <p className="section-message">暂时没有可展示的已发布片段。</p>}
      {!featuredLoading && !featuredError && featured.length > 0 && <div className="clip-grid">
        {featured.map((clip, index) => <ClipCard key={clip.id} clip={clip} index={index} label="随机片段" showFrame
          onOpen={() => openClip(featured, index)} />)}
      </div>}
    </section>

    <footer className="app-footer">台词仅展示已审核且获准预览的素材 · SEARCH · LISTEN · REMEMBER · <a className="footer-share-link" href="/shares">分享管理</a></footer>
    {active.query && <section className="search-results-overlay" role="dialog" aria-modal="true" aria-labelledby="search-results-title">
      <div className="search-results-view">
        <header className="search-overlay-header">
          <div>
            <span className="section-kicker">SEARCH RESULTS</span>
            <h2 id="search-results-title">台词搜索结果</h2>
            <p>关键词：<strong>“{active.query}”</strong>{active.season ? ` · 第 ${active.season} 季` : ''}{active.episode ? ` · 第 ${active.episode} 集` : ''}</p>
          </div>
          <button type="button" className="outline-button search-overlay-close" onClick={closeSearch}>← 返回到主页</button>
        </header>
        <div className="search-overlay-content" aria-live="polite">
          {loading && <div className="section-message" role="status">正在搜索云端台词…</div>}
          {error && <div className="notice-card error-notice" role="alert">
            <span className="notice-icon" aria-hidden="true">!</span>
            <div><h2>{unavailable ? '搜索服务暂时无法连接' : '搜索暂时失败'}</h2>
              <p>{unavailable ? '请检查 Supabase 项目是否可用，再重试。连接失败不会被当成“没有搜索结果”。' : error.message}</p>
              <button type="button" className="text-button" onClick={() => setRetry(value => value + 1)}>重新搜索 ↗</button></div>
          </div>}
          {!loading && !error && visibleData?.total === 0 && <div className="notice-card" role="status">
            <span className="notice-icon" aria-hidden="true">⌕</span>
            <div><h2>没有找到符合条件的台词</h2><p>试试缩短关键词，或清除季、集筛选。未发布或无有效预览授权的素材不会出现在结果里。</p></div>
          </div>}
          {!loading && !error && visibleData && visibleData.total > 0 && <>
            <div className="section-heading results-heading"><div><span className="section-kicker">共找到</span><h2>{visibleData.total} 条台词</h2></div>
              <span className="query-caption">“{visibleData.query}”</span></div>
            <div className="clip-grid search-grid">
              {visibleData.results.map((result: SearchResult, index) => <ClipCard key={result.id} clip={result} index={index} label="搜索结果" query={visibleData.query}
                onOpen={() => openClip(visibleData.results, index)} />)}
            </div>
            <nav className="pagination" aria-label="搜索结果分页">
              <button type="button" className="outline-button" disabled={visibleData.page <= 1} onClick={() => goToPage(visibleData.page - 1)}>← 上一页</button>
              <span>第 {visibleData.page} / {visibleData.pages} 页</span>
              <button type="button" className="outline-button" disabled={visibleData.page >= visibleData.pages} onClick={() => goToPage(visibleData.page + 1)}>下一页 →</button>
            </nav>
          </>}
        </div>
      </div>
    </section>}
    {modal && <ClipModal state={modal} onClose={() => setModal(null)} onMove={moveModal} />}
  </main>;
}
