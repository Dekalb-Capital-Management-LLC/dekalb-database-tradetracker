import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertTriangle } from 'lucide-react'

interface ErrorBoundaryProps {
  children: ReactNode
  fallback?: ReactNode
  label?: string
}

interface ErrorBoundaryState {
  hasError: boolean
}

export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false }

  static getDerivedStateFromError() {
    return { hasError: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[ErrorBoundary${this.props.label ? `: ${this.props.label}` : ''}]`, error, info.componentStack)
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div
            className="flex min-h-32 flex-col items-center justify-center gap-2 rounded-md text-sm"
            style={{ color: '#9f2d22', backgroundColor: '#fff5f4', border: '1px solid #f2c9c3' }}
          >
            <AlertTriangle size={18} aria-hidden="true" />
            <span>{this.props.label ? `${this.props.label} failed to load` : 'Something went wrong'}</span>
          </div>
        )
      )
    }
    return this.props.children
  }
}
