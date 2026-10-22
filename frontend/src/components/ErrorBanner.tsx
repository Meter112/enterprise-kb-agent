export function ErrorBanner({ detail, onDismiss }: { detail: string; onDismiss: () => void }) {
  return (
    <div className="error-banner" role="alert">
      <span className="error-banner-icon">⚠️</span>
      <span className="error-banner-text">生成中断：{detail}</span>
      <button type="button" className="error-banner-close" onClick={onDismiss} aria-label="关闭">
        ×
      </button>
    </div>
  );
}
