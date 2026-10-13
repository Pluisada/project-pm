export const LoadingScreen = ({ message }: { message: string }) => (
  <div className="flex min-h-screen items-center justify-center">
    <div className="text-center">
      <div className="inline-block">
        <div className="h-12 w-12 animate-spin rounded-full border-4 border-[var(--primary-blue)] border-t-transparent"></div>
      </div>
      <p className="mt-4 text-sm text-[var(--gray-text)]">{message}</p>
    </div>
  </div>
);
