package admin

import (
	"context"
	"net/http"

	"github.com/Wei-Shaw/sub2api/internal/pkg/response"
	"github.com/Wei-Shaw/sub2api/internal/service"
	"github.com/gin-gonic/gin"
)

type containerUpdater interface {
	IsContainerUpdate() bool
	StartContainerUpdate(context.Context, service.ContainerUpdateTarget) (*service.ContainerUpdateStatus, error)
	GetContainerUpdateStatus(context.Context) (*service.ContainerUpdateStatus, error)
}

func (h *SystemHandler) startContainerUpdate(c *gin.Context, updater containerUpdater) {
	var target service.ContainerUpdateTarget
	if err := c.ShouldBindJSON(&target); err != nil || len(target.Commit) != 40 || len(target.Digest) != 71 {
		response.Error(c, http.StatusBadRequest, "请先刷新并确认目标版本")
		return
	}
	status, err := updater.StartContainerUpdate(c.Request.Context(), target)
	if err != nil {
		response.Error(c, http.StatusConflict, err.Error())
		return
	}
	response.Success(c, gin.H{"message": "升级任务已提交", "need_restart": false, "accepted": true, "status": status})
}

func (h *SystemHandler) GetUpdateStatus(c *gin.Context) {
	updater, ok := h.updateSvc.(containerUpdater)
	if !ok || !updater.IsContainerUpdate() {
		response.Error(c, http.StatusBadRequest, "当前部署未启用容器升级")
		return
	}
	status, err := updater.GetContainerUpdateStatus(c.Request.Context())
	if err != nil {
		response.Error(c, http.StatusServiceUnavailable, err.Error())
		return
	}
	response.Success(c, status)
}
