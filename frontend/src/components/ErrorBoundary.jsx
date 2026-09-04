import React from 'react';

export class ErrorBoundary extends React.Component {
  state = { hasError: false, error: null };

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error('Uncaught component error:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <main className="sheet" style={{ minHeight: '60vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div className="reading" style={{ maxWidth: '36rem', margin: '2rem auto', border: '1px solid var(--hair)', padding: '2rem', background: 'var(--color-paper-2)' }}>
            <h2 style={{ fontSize: '1.4rem', marginBottom: '0.8rem', color: 'var(--color-ink)' }}>Rendering Error</h2>
            <p style={{ fontSize: '0.95rem', color: 'var(--color-muted)', marginBottom: '1.5rem' }}>
              {this.state.error?.message || 'An unexpected rendering error occurred.'}
            </p>
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => window.location.reload()}
            >
              Reload Page
            </button>
          </div>
        </main>
      );
    }
    return this.props.children;
  }
}
