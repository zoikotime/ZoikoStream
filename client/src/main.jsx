import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { Toaster, toasterProps } from './ui/Toast'
import { initNativeChrome } from './native/bridge'

// Status bar, keyboard behaviour and the splash hand-off, when there is a native shell to
// ask. A no-op in a browser — bridge.js reads window.Capacitor and every call short-circuits
// when it is absent — so this line is safe in both builds and is deliberately NOT awaited:
// blocking the first render on a plugin round trip would trade a styled status bar for a
// slower app.
initNativeChrome()

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
    <Toaster {...toasterProps} />
  </StrictMode>,
)