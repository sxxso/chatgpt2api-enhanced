"use client";

import { Input } from "@/components/ui/input";
import type { UserImageLimits, UserKey } from "@/lib/api";

export type PolicyForm = { daily: string; concurrency: string; models: string; expires: string; blockAll: boolean };
export const emptyPolicy: PolicyForm = { daily: "0", concurrency: "1", models: "", expires: "", blockAll: false };

export function policyForm(item: UserKey): PolicyForm {
  const date = item.expires_at ? new Date(item.expires_at) : null;
  return { daily: String(item.daily_image_limit || 0), concurrency: String(item.max_image_concurrency || 0),
    models: item.allowed_image_models?.join(", ") || "", blockAll: Array.isArray(item.allowed_image_models) && item.allowed_image_models.length === 0,
    expires: date ? new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16) : "" };
}

export function policyPayload(form: PolicyForm): UserImageLimits {
  const daily = Number(form.daily), concurrency = Number(form.concurrency);
  if (!Number.isInteger(daily) || daily < 0 || daily > 1000000 || !Number.isInteger(concurrency) || concurrency < 0 || concurrency > 32) {
    throw new Error("每日额度需为 0–1000000 的整数，并发需为 0–32 的整数");
  }
  return { daily_image_limit: daily, max_image_concurrency: concurrency,
    allowed_image_models: form.blockAll ? [] : form.models.trim() ? form.models.trim().split(/[\s,，]+/) : null,
    expires_at: form.expires ? new Date(form.expires).toISOString() : null };
}

export function UserImagePolicyFields({ value, onChange }: { value: PolicyForm; onChange: (next: PolicyForm) => void }) {
  return <div className="space-y-3">
    <div className="grid grid-cols-2 gap-3">
      <label className="space-y-1 text-sm">每日图片额度<Input aria-label="每日图片额度" type="number" min={0} max={1000000} value={value.daily} onChange={e => onChange({ ...value, daily: e.target.value })} /></label>
      <label className="space-y-1 text-sm">用户图片并发<Input aria-label="用户图片并发" type="number" min={0} max={32} value={value.concurrency} onChange={e => onChange({ ...value, concurrency: e.target.value })} /></label>
    </div>
    <p className="text-xs text-stone-500">额度 0 表示不限，并发 0 跟随全局；按北京时间每天零点重置，仅成功出图计入额度。</p>
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={value.blockAll} onChange={e => onChange({ ...value, blockAll: e.target.checked })} />禁止此用户生图</label>
    <label className="block space-y-1 text-sm">允许的图片模型<Input aria-label="允许的图片模型" disabled={value.blockAll} value={value.models} placeholder="留空允许全部；多个完整型号用逗号分隔" onChange={e => onChange({ ...value, models: e.target.value })} /></label>
    <label className="block space-y-1 text-sm">到期时间<Input aria-label="到期时间" type="datetime-local" value={value.expires} onChange={e => onChange({ ...value, expires: e.target.value })} /></label>
    <p className="text-xs text-stone-500">到期时间按当前设备时区填写，留空长期有效。</p>
  </div>;
}
