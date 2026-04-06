"use client"

import React from "react"
import { cn } from "@/lib/utils"

// ---------- ネイティブ select（既存ページ用）----------

interface NativeSelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  className?: string
}

// ---------- Radix互換コンテキスト（partner/page.tsx 等）----------

const SelectContext = React.createContext<{
  value?: string
  onValueChange?: (v: string) => void
}>({})

// ---------- Select: ネイティブ と Radix 両対応 ----------

type SelectProps =
  | (NativeSelectProps & { onValueChange?: never })
  | {
      value?: string
      defaultValue?: string
      onValueChange: (value: string) => void
      disabled?: boolean
      className?: string
      children?: React.ReactNode
    }

function Select(props: SelectProps) {
  if ("onValueChange" in props && props.onValueChange) {
    const { onValueChange, value, defaultValue, children, className: _cls, disabled: _dis } = props
    return (
      <SelectContext.Provider value={{ value: value ?? defaultValue, onValueChange }}>
        <div data-slot="select" className="relative w-full">
          {children}
        </div>
      </SelectContext.Provider>
    )
  }

  // ネイティブ select
  const { className, children, ...rest } = props as NativeSelectProps
  return (
    <select
      data-slot="select"
      className={cn(
        "flex h-8 w-full appearance-none rounded-lg border border-input bg-background px-2.5 py-1 text-sm text-foreground shadow-xs outline-none transition-shadow focus:border-ring focus:ring-3 focus:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
      {...rest}
    >
      {children}
    </select>
  )
}

// ---------- Radix互換サブコンポーネント ----------

function SelectTrigger({ className, children, id }: React.HTMLAttributes<HTMLDivElement> & { id?: string }) {
  const { value, onValueChange } = React.useContext(SelectContext)
  const [open, setOpen] = React.useState(false)

  React.useEffect(() => {
    if (!open) return
    const handler = () => setOpen(false)
    document.addEventListener("click", handler)
    return () => document.removeEventListener("click", handler)
  }, [open])

  return (
    <div className="relative w-full">
      <button
        id={id}
        data-slot="select-trigger"
        type="button"
        onClick={(e) => { e.stopPropagation(); setOpen(!open) }}
        className={cn(
          "flex h-8 w-full items-center justify-between rounded-lg border border-input bg-background px-2.5 py-1 text-sm",
          className
        )}
      >
        {children}
      </button>
      {open && (
        <div
          data-slot="select-dropdown"
          className="absolute z-50 mt-1 w-full rounded-lg border border-input bg-background shadow-md"
          onClick={(e) => e.stopPropagation()}
        >
          <SelectDropdownContext.Provider value={{ onSelect: (v) => { onValueChange?.(v); setOpen(false) }, currentValue: value }}>
            {/* SelectContent がここに入る想定 — 実際には portal不要なのでこのままでOK */}
          </SelectDropdownContext.Provider>
        </div>
      )}
    </div>
  )
}

const SelectDropdownContext = React.createContext<{
  onSelect?: (v: string) => void
  currentValue?: string
}>({})

function SelectValue({ placeholder }: { placeholder?: string }) {
  const { value } = React.useContext(SelectContext)
  return (
    <span data-slot="select-value" className={value ? "" : "text-muted-foreground"}>
      {value || placeholder}
    </span>
  )
}

function SelectContent({ className, children }: React.HTMLAttributes<HTMLDivElement>) {
  const { value, onValueChange } = React.useContext(SelectContext)
  return (
    <div data-slot="select-content" className={cn("py-1", className)}>
      {React.Children.map(children, (child) => {
        if (!React.isValidElement(child)) return child
        return React.cloneElement(
          child as React.ReactElement<{ _onSelect?: (v: string) => void; _currentValue?: string }>,
          { _onSelect: onValueChange, _currentValue: value }
        )
      })}
    </div>
  )
}

function SelectItem({
  className,
  children,
  value,
  _onSelect,
  _currentValue,
}: React.HTMLAttributes<HTMLDivElement> & {
  value: string
  _onSelect?: (v: string) => void
  _currentValue?: string
}) {
  return (
    <div
      data-slot="select-item"
      data-value={value}
      onClick={() => _onSelect?.(value)}
      className={cn(
        "relative flex cursor-pointer select-none items-center rounded px-2 py-1.5 text-sm outline-none hover:bg-accent",
        _currentValue === value && "bg-accent font-medium",
        className
      )}
    >
      {children}
    </div>
  )
}

export { Select, SelectTrigger, SelectValue, SelectContent, SelectItem }
