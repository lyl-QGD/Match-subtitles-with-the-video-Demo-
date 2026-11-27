import { useCallback, useEffect, useState } from 'react';
import { ApiError, ManagedShare, managedShares, revokeManagedShare } from '../lib/api';

function formatTime(ms: number) {
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.floor((ms % 60_000) / 1000);
  const millis = ms % 1000;
  return `${minutes}:${String(seconds).padStart(2, '0')}.${String(millis).padStart(3, '0')}`;
}

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : '未设置';
}

export function ShareManager() {
  const [items, setItems] = useState<ManagedShare[]>([]);
  const [loading, setLoading] = useState(true);
  const [pendingId, setPendingId] = useState('');
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setItems(await managedShares());
    } catch (cause) {
      setError(cause instanceof ApiError && cause.code === 'local_management_only'
        ? '分享管理仅在本机开发环境开放。'
        : cause instanceof Error ? cause.message : '分享列表暂时无法加载。');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function revoke(item: ManagedShare) {
    if (item.status !== 'active' || !window.confirm('撤销后，这个分享链接将立即失效。确定继续吗？')) return;
    setPendingId(item.id);
    setError('');
    try {
      await revokeManagedShare(item.id);
      setItems(current => current.map(row => row.id === item.id ? { ...row, status: 'revoked' } : row));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '撤销分享失败，请重试。');
    } finally {
      setPendingId('');
    }
  }

  return <main className="share-manager-shell">
    <header className="share-manager-header">
      <div><span className="section-kicker">LOCAL SHARE MANAGEMENT</span><h1>分享管理</h1>
        <p>分享记录保存在 Supabase。原始分享口令不会保存或显示；关闭浏览器后，可在这里撤销已创建的链接。</p></div>
      <a className="outline-button" href="/">← 返回到主页</a>
    </header>
    {error && <div className="notice-card error-notice" role="alert"><span className="notice-icon">!</span><div><h2>分享列表暂不可用</h2><p>{error}</p><button type="button" className="text-button" onClick={() => void load()}>重试</button></div></div>}
    {loading && <p className="section-message">正在读取云端分享记录…</p>}
    {!loading && !error && items.length === 0 && <div className="notice-card" role="status"><span className="notice-icon">↗</span><div><h2>还没有分享记录</h2><p>打开一个台词片段，在播放器中创建分享链接后，记录会显示在这里。</p></div></div>}
    {!loading && !error && items.length > 0 && <div className="share-manager-list">
      {items.map(item => <article className="share-manager-card" key={item.id}>
        <div className="share-manager-card-title"><div><span className="section-kicker">{item.series_slug} · S{String(item.season).padStart(2, '0')}E{String(item.episode).padStart(2, '0')}</span>
          <h2>{formatTime(item.start_ms)} — {formatTime(item.end_ms)}</h2></div>
          <span className={`share-status status-${item.status}`}>{item.status === 'active' ? '有效' : item.status === 'revoked' ? '已撤销' : '已过期'}</span></div>
        <p>创建时间：{formatDate(item.created_at)}　·　到期时间：{formatDate(item.expires_at)}</p>
        {item.status === 'active' && <button type="button" className="share-revoke-button" disabled={pendingId === item.id} onClick={() => void revoke(item)}>{pendingId === item.id ? '正在撤销…' : '撤销链接'}</button>}
      </article>)}
    </div>}
    <footer className="app-footer">此管理页仅限本机开发环境使用 · 公开部署前需实现用户身份验证与管理权限</footer>
  </main>;
}
