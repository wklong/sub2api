import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ContainerUpdatePanel from '../ContainerUpdatePanel.vue'
import { getUpdateStatus, performUpdate, type VersionInfo } from '@/api/admin/system'

vi.mock('@/api/admin/system', () => ({ getUpdateStatus: vi.fn(), performUpdate: vi.fn() }))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))

const info: VersionInfo = {
  current_version: 'dev', latest_version: 'main-new', current_commit: 'c'.repeat(40),
  has_update: true, cached: false, build_type: 'release', update_mode: 'container',
  update_target: { commit: 'a'.repeat(40), digest: 'sha256:' + 'b'.repeat(64),
    image: 'image@digest', published_at: 'today', html_url: 'https://github.com/wklong/sub2api' }
}

describe('ContainerUpdatePanel', () => {
  beforeEach(() => { vi.useFakeTimers(); vi.mocked(getUpdateStatus).mockResolvedValue({ phase: 'idle' }) })
  afterEach(() => { vi.useRealTimers(); vi.clearAllMocks() })

  it('requires confirmation and submits the displayed immutable target', async () => {
    vi.mocked(performUpdate).mockResolvedValue({ message: 'queued', need_restart: false,
      accepted: true, status: { phase: 'queued' } })
    const wrapper = mount(ContainerUpdatePanel, { props: { info } })
    await flushPromises()
    await wrapper.get('button').trigger('click')
    expect(performUpdate).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('version.customConfirm')
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(performUpdate).toHaveBeenCalledWith(info.update_target)
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('recovers polling after a container disconnect and reports verified success', async () => {
    vi.mocked(getUpdateStatus).mockResolvedValueOnce({ phase: 'recreating' })
      .mockRejectedValueOnce(new Error('connection lost')).mockResolvedValueOnce({ phase: 'succeeded' })
    const wrapper = mount(ContainerUpdatePanel, { props: { info } })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(2500)
    expect(wrapper.emitted('refreshed')).toBeUndefined()
    await vi.advanceTimersByTimeAsync(2500)
    expect(wrapper.emitted('refreshed')).toHaveLength(1)
    wrapper.unmount()
  })

  it('shows verification failure and offers no upgrade when the image is unavailable', async () => {
    const wrapper = mount(ContainerUpdatePanel, { props: { info: { ...info, warning: 'manifest unavailable', has_update: false } } })
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toBe('manifest unavailable')
    expect(wrapper.find('button').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('version.upToDate')
    wrapper.unmount()
  })

  it('resumes an active task after reopening without allowing a duplicate submission', async () => {
    vi.mocked(getUpdateStatus).mockResolvedValue({ phase: 'pulling' })
    const wrapper = mount(ContainerUpdatePanel, { props: { info } })
    await flushPromises()
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    wrapper.unmount()
    await vi.advanceTimersByTimeAsync(5000)
    expect(getUpdateStatus).toHaveBeenCalledTimes(1)
  })
})
