import React from 'react';
export default class DashboardErrorBoundary extends React.Component {
 state={error:null};
 static getDerivedStateFromError(error){return {error}}
 componentDidCatch(error,info){console.error('Dashboard page error',error,info.componentStack)}
 componentDidUpdate(previous){if(previous.resetKey!==this.props.resetKey&&this.state.error)this.setState({error:null})}
 render(){
  if(!this.state.error)return this.props.children;
  return <section className="dashboard-recovery" role="alert"><h2>This page could not be displayed</h2><p>Your saved reports are still available. Retry this page or reload the dashboard.</p><details><summary>Error details</summary><code>{this.state.error?.message||'Unexpected page error'}</code></details><div><button className="primary" onClick={()=>this.setState({error:null})}>Retry page</button><button className="secondary" onClick={()=>window.location.reload()}>Reload dashboard</button></div></section>
 }
}
