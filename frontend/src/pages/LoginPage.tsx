import { ArrowRight, LockKeyhole } from 'lucide-react'
import { motion } from 'motion/react'
import { FormEvent, useState } from 'react'

import { ApiError } from '../api'
import { useAuth } from '../auth'

export function LoginPage() {
  const { bootstrapAvailable, bootstrap, login } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [bootstrapToken, setBootstrapToken] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setSubmitting(true)
    setError('')
    try {
      if (bootstrapAvailable) await bootstrap(username, password, bootstrapToken)
      else await login(username, password)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Sign-in failed.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="grid min-h-screen lg:grid-cols-[minmax(0,1.15fr)_minmax(24rem,0.85fr)]">
      <section className="relative hidden overflow-hidden bg-ink p-12 text-white lg:flex lg:flex-col lg:justify-between">
        <div className="absolute -right-40 -top-40 size-[34rem] rounded-full border border-fern/20" />
        <div className="absolute -right-16 -top-16 size-[20rem] rounded-full border border-fern/30" />
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-fern">Learning-Control-Center</p>
        <div className="relative max-w-2xl">
          <p className="mb-5 font-mono text-xs uppercase tracking-[0.2em] text-fern">Evidence over intention</p>
          <h1 className="font-display text-6xl font-semibold leading-[1.03] tracking-[-0.04em]">
            Know what to learn next—and why.
          </h1>
          <p className="mt-7 max-w-xl text-lg leading-8 text-white/55">
            A private control surface for deliberate practice, competency verification, and deterministic progress.
          </p>
        </div>
        <p className="text-sm text-white/35">Self-hosted · Single-user · AI-independent</p>
      </section>
      <section className="flex items-center justify-center px-5 py-12 sm:px-10">
        <motion.div
          className="w-full max-w-md"
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.28 }}
        >
          <span className="mb-8 grid size-12 place-items-center rounded-2xl bg-moss text-white shadow-soft">
            <LockKeyhole className="size-5" />
          </span>
          <p className="eyebrow mb-3">{bootstrapAvailable ? 'Protected first setup' : 'Private workspace'}</p>
          <h2 className="font-display text-4xl font-semibold tracking-tight">
            {bootstrapAvailable ? 'Create the first account' : 'Welcome back'}
          </h2>
          <p className="mt-3 text-sm leading-6 text-ink/55">
            {bootstrapAvailable
              ? 'Use the server-provided bootstrap secret. Public registration remains disabled.'
              : 'Sign in to continue your learning loop.'}
          </p>
          <form className="mt-8 space-y-4" onSubmit={(event) => void submit(event)}>
            <label className="block text-sm font-medium">
              Username
              <input
                className="field mt-2"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoComplete="username"
                required
              />
            </label>
            <label className="block text-sm font-medium">
              Password
              <input
                className="field mt-2"
                type="password"
                minLength={12}
                maxLength={256}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete={bootstrapAvailable ? 'new-password' : 'current-password'}
                required
              />
            </label>
            {bootstrapAvailable ? (
              <label className="block text-sm font-medium">
                Bootstrap secret
                <input
                  className="field mt-2 font-mono"
                  type="password"
                  value={bootstrapToken}
                  onChange={(event) => setBootstrapToken(event.target.value)}
                  autoComplete="off"
                  required
                />
              </label>
            ) : null}
            {error ? <p className="rounded-xl bg-rose-50 p-3 text-sm text-rose-800" role="alert">{error}</p> : null}
            <button className="button-primary mt-2 w-full" disabled={submitting}>
              {submitting ? 'Please wait…' : bootstrapAvailable ? 'Create account' : 'Sign in'}
              <ArrowRight className="size-4" aria-hidden="true" />
            </button>
          </form>
        </motion.div>
      </section>
    </main>
  )
}
