import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

// NOTE: StrictMode intentionally removed.
// React 18 StrictMode double-invokes effects in dev, which can cause
// issues with reactflow + async state updates in this app.
ReactDOM.createRoot(document.getElementById('root')!).render(<App />)
