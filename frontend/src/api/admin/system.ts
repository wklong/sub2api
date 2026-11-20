/**
 * System API endpoints for admin operations
 */

import { apiClient } from '../client'

export interface ReleaseInfo {
  name: string
  body: string
  published_at: string
  html_url: string
}

export interface ContainerUpdateTarget {
  commit: string
  digest: string
  image: string
  published_at: string
  html_url: string
}

export interface ContainerUpdateStatus {
  phase: string
  message?: string
  target?: ContainerUpdateTarget
  previous_image_id?: string
  previous_image?: string
  updated_at?: string
}

export async function getUpdateStatus(): Promise<ContainerUpdateStatus> {
  const { data } = await apiClient.get<ContainerUpdateStatus>('/admin/system/update-status')
  return data
}

export interface VersionInfo {
  update_mode?: string
  current_commit?: string
  update_target?: ContainerUpdateTarget
  current_version: string
  latest_version: string
  has_update: boolean
  release_info?: ReleaseInfo
  cached: boolean
  warning?: string
  build_type: string // "source" for manual builds, "release" for CI builds
}

/**
 * Get current version
 */
export async function getVersion(): Promise<{ version: string }> {
  const { data } = await apiClient.get<{ version: string }>('/admin/system/version')
  return data
}

/**
 * Check for updates
 * @param force - Force refresh from GitHub API
 */
export async function checkUpdates(force = false): Promise<VersionInfo> {
  const { data } = await apiClient.get<VersionInfo>('/admin/system/check-updates', {
    params: force ? { force: 'true' } : undefined,
    timeout: 100 * 1000
  })
  return data
}

export interface UpdateResult {
  accepted?: boolean
  status?: ContainerUpdateStatus
  message: string
  need_restart: boolean
}

export interface RollbackVersionInfo {
  version: string
  published_at: string
  html_url: string
}

/**
 * Get versions available for rollback (up to 3 versions older than current)
 */
export async function getRollbackVersions(): Promise<{ versions: RollbackVersionInfo[] }> {
  const { data } = await apiClient.get<{ versions: RollbackVersionInfo[] }>(
    '/admin/system/rollback-versions'
  )
  return data
}

/**
 * In-place update/rollback downloads a full release binary from GitHub, which
 * can take several minutes on slow links. The global 30s axios timeout would
 * abort the request mid-download (#4504), so these calls wait as long as the
 * backend allows (15 minutes server-side).
 */
const UPDATE_REQUEST_TIMEOUT_MS = 15 * 60 * 1000

/**
 * Perform system update
 * Downloads and applies the latest version
 */
export async function performUpdate(target?: ContainerUpdateTarget): Promise<UpdateResult> {
  const { data } = await apiClient.post<UpdateResult>('/admin/system/update', target, {
    timeout: UPDATE_REQUEST_TIMEOUT_MS
  })
  return data
}

/**
 * Rollback to a previous version
 * @param version - Target version (e.g. "0.1.146"); omit to restore the local backup binary
 */
export async function rollback(version?: string): Promise<UpdateResult> {
  const { data } = await apiClient.post<UpdateResult>(
    '/admin/system/rollback',
    version ? { version } : undefined,
    { timeout: UPDATE_REQUEST_TIMEOUT_MS }
  )
  return data
}

/**
 * Restart the service
 */
export async function restartService(): Promise<{ message: string }> {
  const { data } = await apiClient.post<{ message: string }>('/admin/system/restart')
  return data
}

export const systemAPI = {
  getVersion,
  checkUpdates,
  performUpdate,
  getRollbackVersions,
  rollback,
  restartService
}

export default systemAPI
