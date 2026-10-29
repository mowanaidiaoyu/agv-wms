import { clsx } from 'clsx'

export function Surface({
  title,
  subtitle,
  children,
  className,
  action,
}: {
  title?: string
  subtitle?: string
  children: React.ReactNode
  className?: string
  action?: React.ReactNode
}) {
  return (
    <section className={clsx('surface-card', className)}>
      {title || subtitle || action ? (
        <div className="surface-header">
          <div>
            {title ? <p className="surface-title">{title}</p> : null}
            {subtitle ? <p className="surface-subtitle">{subtitle}</p> : null}
          </div>
          {action}
        </div>
      ) : null}
      {children}
    </section>
  )
}
