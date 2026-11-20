<template>
  <div class="space-y-3 text-sm">
    <p class="font-medium">{{ t('version.customTitle') }}</p>
    <dl class="space-y-1 break-all text-xs text-gray-600 dark:text-dark-300">
      <div>{{ t('version.customCurrent') }}: {{ info.current_commit || info.current_version }}</div>
      <div v-if="info.update_target">{{ t('version.customTarget') }}: {{ info.update_target.commit }}</div>
      <div v-if="info.update_target">{{ t('version.customPublished') }}: {{ info.update_target.published_at }}</div>
    </dl>
    <p v-if="info.warning" role="alert" class="break-words text-amber-700 dark:text-amber-300">{{ info.warning }}</p>
    <p v-if="error" role="alert" class="break-words text-red-600">{{ error }}</p>
    <div v-if="status && status.phase !== 'idle'" role="status" class="space-y-1 rounded bg-gray-100 p-2 dark:bg-dark-700">
      <p>{{ t(`version.customPhase.${status.phase}`) }}</p>
      <p class="break-words text-xs">{{ status.message }}</p>
    </div>
    <p v-if="busy" class="text-xs">{{ t('version.customReconnect') }}</p>
    <template v-if="confirming">
      <p class="text-xs">{{ t('version.customConfirm') }}</p>
      <button class="btn btn-primary w-full" :disabled="busy" @click="start">{{ t('version.customConfirmButton') }}</button>
      <button class="btn btn-secondary w-full" @click="confirming = false">{{ t('common.cancel') }}</button>
    </template>
    <button v-else-if="info.has_update && !info.warning" class="btn btn-primary w-full" :disabled="busy" @click="confirming = true">
      {{ t('version.customUpgrade') }}
    </button>
    <p v-else-if="!info.warning && !busy" class="text-xs">{{ t('version.upToDate') }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getUpdateStatus, performUpdate, type ContainerUpdateStatus, type VersionInfo } from '@/api/admin/system'

const props = defineProps<{ info: VersionInfo }>()
const emit = defineEmits<{ refreshed: [] }>()
const { t } = useI18n()
const status = ref<ContainerUpdateStatus | null>(null)
const confirming = ref(false)
const submitting = ref(false)
const error = ref('')
const busy = computed(() => submitting.value || ['queued', 'pulling', 'recreating', 'checking'].includes(status.value?.phase || ''))
let timer: ReturnType<typeof setTimeout> | undefined
let disposed = false
let disconnectedAt = 0

async function poll() {
  try {
    const previous = status.value?.phase
    status.value = await getUpdateStatus()
    disconnectedAt = 0
    if (status.value.phase === 'succeeded' && (previous && previous !== status.value.phase ||
      status.value.target && status.value.target.commit !== props.info.current_commit)) emit('refreshed')
  } catch (cause) {
    if (!disconnectedAt) disconnectedAt = Date.now()
    // 容器重建时连接会断开，持续重连；超时仅告知状态未知，不推断升级失败。
    if (Date.now() - disconnectedAt > 6 * 60 * 1000) error.value = t('version.customStatusUnknown')
  } finally {
    if (!disposed) timer = setTimeout(poll, 2500)
  }
}

async function start() {
  if (busy.value || !props.info.update_target) return
  submitting.value = true
  error.value = ''
  confirming.value = false
  try {
    const result = await performUpdate(props.info.update_target)
    if (result.status) status.value = result.status
  } catch (cause) {
    const e = cause as { response?: { data?: { message?: string } }; message?: string }
    error.value = e.response?.data?.message || e.message || t('version.updateFailed')
    // 请求中断不能证明任务未提交，轮询会恢复真实状态。
  } finally {
    submitting.value = false
  }
}

onMounted(poll)
onBeforeUnmount(() => { disposed = true; if (timer) clearTimeout(timer) })
</script>
