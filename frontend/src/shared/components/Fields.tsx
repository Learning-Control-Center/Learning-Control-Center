import { forwardRef, type InputHTMLAttributes, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react'

type FieldFrameProps = { id: string; label: string; description?: string; error?: string; children: React.ReactNode }

function FieldFrame({ id, label, description, error, children }: FieldFrameProps) {
  const descriptionId = description ? `${id}-description` : undefined
  const errorId = error ? `${id}-error` : undefined
  return <div><label className="mb-1.5 block text-sm font-semibold" htmlFor={id}>{label}</label>{description ? <p id={descriptionId} className="mb-2 text-sm text-ink/60">{description}</p> : null}{children}{error ? <p id={errorId} className="mt-1.5 text-sm font-medium text-status-critical">{error}</p> : null}</div>
}

export const TextField = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { label: string; description?: string; error?: string }>(function TextField({ id, label, description, error, ...props }, ref) {
  const fieldId = id ?? props.name ?? label.toLowerCase().replace(/\W+/g, '-')
  return <FieldFrame id={fieldId} label={label} description={description} error={error}><input ref={ref} id={fieldId} className="field" aria-invalid={error ? true : undefined} aria-describedby={[description ? `${fieldId}-description` : '', error ? `${fieldId}-error` : ''].filter(Boolean).join(' ') || undefined} {...props} /></FieldFrame>
})

export const SelectField = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement> & { label: string; description?: string; error?: string }>(function SelectField({ id, label, description, error, children, ...props }, ref) {
  const fieldId = id ?? props.name ?? label.toLowerCase().replace(/\W+/g, '-')
  return <FieldFrame id={fieldId} label={label} description={description} error={error}><select ref={ref} id={fieldId} className="field" aria-invalid={error ? true : undefined} aria-describedby={[description ? `${fieldId}-description` : '', error ? `${fieldId}-error` : ''].filter(Boolean).join(' ') || undefined} {...props}>{children}</select></FieldFrame>
})

export const TextAreaField = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement> & { label: string; description?: string; error?: string }>(function TextAreaField({ id, label, description, error, ...props }, ref) {
  const fieldId = id ?? props.name ?? label.toLowerCase().replace(/\W+/g, '-')
  return <FieldFrame id={fieldId} label={label} description={description} error={error}><textarea ref={ref} id={fieldId} className="field" aria-invalid={error ? true : undefined} aria-describedby={[description ? `${fieldId}-description` : '', error ? `${fieldId}-error` : ''].filter(Boolean).join(' ') || undefined} {...props} /></FieldFrame>
})
