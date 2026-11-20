import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import DashboardErrorBoundary from './DashboardErrorBoundary.jsx'
import './styles.css'
import './compact.css'
import './gp-report.css'
import './sa-theme.css'
import './responsive.css'

createRoot(document.getElementById('root')).render(<React.StrictMode><DashboardErrorBoundary><App/></DashboardErrorBoundary></React.StrictMode>)
