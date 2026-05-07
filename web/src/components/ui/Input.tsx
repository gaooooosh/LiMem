import { forwardRef, type InputHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...rest }, ref) => (
    <input
      ref={ref}
      className={cn(
        "h-9 w-full rounded-md border border-border bg-panel px-3 text-sm text-text",
        "placeholder:text-subtle",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1 focus-visible:ring-offset-bg",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...rest}
    />
  ),
);
Input.displayName = "Input";

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...rest }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        "w-full rounded-md border border-border bg-panel px-3 py-2 text-sm text-text",
        "placeholder:text-subtle font-mono",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1 focus-visible:ring-offset-bg",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...rest}
    />
  ),
);
Textarea.displayName = "Textarea";
