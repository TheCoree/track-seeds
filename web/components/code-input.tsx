"use client";

import { REGEXP_ONLY_DIGITS } from "input-otp";
import { InputOTP, InputOTPGroup, InputOTPSeparator, InputOTPSlot } from "@/components/ui/input-otp";

/** Код из приложения-аутентификатора: 6 клеток, вставка из буфера, автоподстановка на телефоне. */
export function CodeInput({ value, onChange, onComplete, autoFocus }: { value: string; onChange: (value: string) => void; onComplete?: (value: string) => void; autoFocus?: boolean }) {
  return (
    <InputOTP
      maxLength={6}
      pattern={REGEXP_ONLY_DIGITS}
      inputMode="numeric"
      autoComplete="one-time-code"
      autoFocus={autoFocus}
      value={value}
      onChange={onChange}
      onComplete={onComplete}
      containerClassName="justify-center"
      aria-label="Код из приложения"
    >
      <InputOTPGroup>
        {[0, 1, 2].map((i) => (
          <InputOTPSlot key={i} index={i} className="size-11 text-lg" />
        ))}
      </InputOTPGroup>
      <InputOTPSeparator />
      <InputOTPGroup>
        {[3, 4, 5].map((i) => (
          <InputOTPSlot key={i} index={i} className="size-11 text-lg" />
        ))}
      </InputOTPGroup>
    </InputOTP>
  );
}
