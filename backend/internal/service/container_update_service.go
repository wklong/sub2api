package service

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"regexp"
	"time"
)

// The application has access only to the updater socket, never the Docker socket.
func (s *UpdateService) IsContainerUpdate() bool {
	return os.Getenv("SUB2API_UPDATE_MODE") == "container"
}

type ContainerUpdateTarget struct {
	Commit      string `json:"commit"`
	Digest      string `json:"digest"`
	Image       string `json:"image"`
	PublishedAt string `json:"published_at"`
	HTMLURL     string `json:"html_url"`
}

type ContainerUpdateStatus struct {
	Phase           string                 `json:"phase"`
	Message         string                 `json:"message"`
	Target          *ContainerUpdateTarget `json:"target,omitempty"`
	PreviousImageID string                 `json:"previous_image_id,omitempty"`
	PreviousImage   string                 `json:"previous_image,omitempty"`
	UpdatedAt       string                 `json:"updated_at,omitempty"`
}

func (s *UpdateService) updaterRequest(ctx context.Context, method, path string, input, output any) error {
	var body io.Reader
	if input != nil {
		data, err := json.Marshal(input)
		if err != nil {
			return err
		}
		body = bytes.NewReader(data)
	}
	transport := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{Timeout: 3 * time.Second}).DialContext(ctx, "unix", "/run/sub2api-updater/updater.sock")
	}}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: 90 * time.Second}
	req, err := http.NewRequestWithContext(ctx, method, "http://updater"+path, body)
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("宿主机升级服务不可用，请先按部署说明安装并启动 sub2api-updater")
	}
	defer func() { _ = resp.Body.Close() }()
	reader := io.LimitReader(resp.Body, 1024*1024)
	if resp.StatusCode >= 300 {
		var detail struct {
			Message string `json:"message"`
		}
		if json.NewDecoder(reader).Decode(&detail) == nil && detail.Message != "" {
			return fmt.Errorf("%s", detail.Message)
		}
		return fmt.Errorf("升级服务请求失败（HTTP %d）", resp.StatusCode)
	}
	return json.NewDecoder(reader).Decode(output)
}

func (s *UpdateService) checkContainerUpdate(ctx context.Context, force bool) (*UpdateInfo, error) {
	commit := os.Getenv("SUB2API_COMMIT")
	info := &UpdateInfo{CurrentVersion: s.currentVersion, LatestVersion: s.currentVersion,
		BuildType: s.buildType, UpdateMode: "container", CurrentCommit: commit}
	var target ContainerUpdateTarget
	path := "/latest"
	if force {
		path += "?force=true"
	}
	if err := s.updaterRequest(ctx, http.MethodGet, path, nil, &target); err != nil {
		info.Warning = err.Error()
		return info, nil
	}
	if !regexp.MustCompile(`^[0-9a-f]{40}$`).MatchString(target.Commit) || !regexp.MustCompile(`^sha256:[0-9a-f]{64}$`).MatchString(target.Digest) {
		info.Warning = "升级服务返回了无效的镜像版本"
		return info, nil
	}
	info.UpdateTarget = &target
	info.LatestVersion = "main-" + target.Commit[:12]
	info.HasUpdate = commit != target.Commit
	info.ReleaseInfo = &ReleaseInfo{Name: info.LatestVersion, PublishedAt: target.PublishedAt,
		HTMLURL: target.HTMLURL, Body: "wklong/sub2api main 已成功构建并验证可拉取的自有镜像"}
	return info, nil
}

func (s *UpdateService) StartContainerUpdate(ctx context.Context, target ContainerUpdateTarget) (*ContainerUpdateStatus, error) {
	var status ContainerUpdateStatus
	err := s.updaterRequest(ctx, http.MethodPost, "/update", target, &status)
	return &status, err
}

func (s *UpdateService) GetContainerUpdateStatus(ctx context.Context) (*ContainerUpdateStatus, error) {
	var status ContainerUpdateStatus
	err := s.updaterRequest(ctx, http.MethodGet, "/status", nil, &status)
	return &status, err
}
