import React from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './routes/App';
import { Review } from './routes/Review';
import { ShareManager } from './routes/ShareManager';
import { SharePage } from './routes/SharePage';
import './style.css';
const shareToken = location.pathname.match(/^\/s\/([^/]+)\/?$/)?.[1];
createRoot(document.getElementById('root')!).render(<React.StrictMode>
  {location.pathname === '/review' ? <Review />
    : location.pathname === '/shares' ? <ShareManager />
      : shareToken ? <SharePage token={shareToken} />
        : <App />}
</React.StrictMode>);
