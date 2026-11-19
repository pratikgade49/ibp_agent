import React from 'react';
import ReactDOM from 'react-dom/client';
import AuthApp from './auth/AuthApp';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AuthApp />
  </React.StrictMode>,
);
