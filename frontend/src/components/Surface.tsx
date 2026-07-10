import { clsx } from 'clsx'

export function Surface({
  title,
  subtitle,
  children,
  className,
  action,
}: {
  title: string
  subtitle?: string
  children: React.ReactNode
  className?: string
  action?: React.ReactNode
}) {
  return (
    <section className={clsx('surface-card', className)}>
      <div className="surface-header">
        <div>
          <p className="surface-title">{title}</p>
          {subtitle ? <p className="surface-subtitle">{subtitle}</p> : null}
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}
