import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { Toaster, toasterProps } from './ui/Toast'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
    <Toaster {...toasterProps} />
  </StrictMode>,
)