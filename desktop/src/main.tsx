import { createRoot } from 'react-dom/client'
import App from './App'
import './shell.css'

try {
  const tokens = JSON.parse(localStorage.getItem('desktop.theme') || '{}')
  for (const [name, value] of Object.entries(tokens))
    document.documentElement.style.setProperty(name, String(value))
} catch {
  /* A fresh installation starts with the warm skin. */
}
createRoot(document.getElementById('root')!).render(<App />)
