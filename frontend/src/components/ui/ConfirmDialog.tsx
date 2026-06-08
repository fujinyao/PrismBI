'use client'

import { Modal } from './Modal'
import { Button } from './Button'
import { useI18nStore } from '@/stores/i18nStore'

interface ConfirmDialogProps {
  open: boolean
  onClose: () => void
  onConfirm: () => void
  title: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  variant?: 'danger' | 'primary'
  loading?: boolean
}

export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  message,
  confirmLabel: confirmLabelProp,
  cancelLabel: cancelLabelProp,
  variant = 'danger',
  loading,
}: ConfirmDialogProps) {
  const t = useI18nStore((s) => s.t)
  const confirmLabel = confirmLabelProp ?? t('common.confirm', 'Confirm')
  const cancelLabel = cancelLabelProp ?? t('common.cancel', 'Cancel')
  return (
    <Modal open={open} onClose={onClose} title={title} size="sm">
      <p className="text-sm text-gray-600 dark:text-gray-400">{message}</p>
      <div className="mt-6 flex justify-end gap-3">
        <Button variant="ghost" size="md" onClick={onClose} disabled={loading}>
          {cancelLabel}
        </Button>
        <Button
          variant={variant === 'danger' ? 'danger' : 'primary'}
          size="md"
          onClick={onConfirm}
          loading={loading}
        >
          {confirmLabel}
        </Button>
      </div>
    </Modal>
  )
}
