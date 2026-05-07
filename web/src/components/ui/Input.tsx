import { forwardRef, type InputHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

const baseField = [
  "w-full rounded-lg border border-border bg-panel text-sm text-text",
  "placeholder:text-subtle/80",
  "shadow-[inset_0_1px_0_0_hsl(var(--border)/0.4)]",
  "transition-[box-shadow,border-color,background] duration-150 ease-out-soft",
  "hover:border-border-strong",
  "focus-visible:outline-none focus-visible:border-accent focus-visible:ring-4 focus-visible:ring-accent/15",
  "disabled:cursor-not-allowed disabled:bg-muted disabled:opacity-60",
].join(" ");

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...rest }, ref) => (
    <input
      ref={ref}
      className={cn(baseField, "h-10 px-3", className)}
      {...rest}
    />
  ),
);
Input.displayName = "Input";

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...rest }, ref) => (
    <textarea
      ref={ref}
      className={cn(baseField, "px-3 py-2 font-mono leading-relaxed", className)}
      {...rest}
    />
  ),
);
Textarea.displayName = "Textarea";
