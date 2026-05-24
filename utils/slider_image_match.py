#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NoCaptcha 滑块缺口定位 - 基于 OpenCV 图像匹配
参考: GeekedTest (xKiian/GeekedTest) 的 Canny + matchTemplate 方案
"""

import cv2
import numpy as np
from typing import Optional, Tuple
from loguru import logger


class SliderImageMatcher:
    """滑块缺口图像匹配器"""

    @staticmethod
    def find_gap_position(
        background: np.ndarray,
        puzzle_piece: np.ndarray,
        offset_correction: int = -35,
    ) -> Optional[int]:
        """
        使用 Canny 边缘检测 + 模板匹配定位滑块缺口位置。

        Args:
            background: 背景图 (numpy array, BGR 或灰度)
            puzzle_piece: 滑块缺口图 (numpy array, BGR 或灰度)
            offset_correction: 坐标修正值（默认 -35，匹配算法
                               找到的是中心点，需修正到缺口起点）

        Returns:
            缺口 X 坐标（像素），失败返回 None
        """
        try:
            # 统一转为灰度图
            if len(background.shape) == 3:
                bg_gray = cv2.cvtColor(background, cv2.COLOR_BGR2GRAY)
            else:
                bg_gray = background

            if len(puzzle_piece.shape) == 3:
                piece_gray = cv2.cvtColor(puzzle_piece, cv2.COLOR_BGR2GRAY)
            else:
                piece_gray = puzzle_piece

            # Canny 边缘检测
            edge_bg = cv2.Canny(bg_gray, 100, 200)
            edge_piece = cv2.Canny(piece_gray, 100, 200)

            # 转为 3 通道用于 matchTemplate
            edge_bg_rgb = cv2.cvtColor(edge_bg, cv2.COLOR_GRAY2RGB)
            edge_piece_rgb = cv2.cvtColor(edge_piece, cv2.COLOR_GRAY2RGB)

            # 模板匹配
            result = cv2.matchTemplate(
                edge_bg_rgb, edge_piece_rgb, cv2.TM_CCOEFF_NORMED
            )

            # 找到最佳匹配位置
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
            confidence = max_val

            if confidence < 0.3:
                logger.warning(
                    f"图像匹配置信度过低: {confidence:.2f}，"
                    f"可能匹配失败"
                )

            # max_loc 是缺口左上角
            piece_h, piece_w = edge_piece.shape[:2]
            center_x = max_loc[0] + piece_w // 2

            # 修正到滑块实际需要滑动的距离
            gap_x = center_x + offset_correction

            logger.info(
                f"图像匹配结果: 中心={center_x}px, "
                f"修正后={gap_x}px, 置信度={confidence:.3f}"
            )

            return max(0, gap_x)

        except Exception as e:
            logger.error(f"图像匹配失败: {e}")
            return None

    @staticmethod
    def find_gap_from_bytes(
        bg_bytes: bytes,
        piece_bytes: bytes,
        offset_correction: int = -35,
    ) -> Optional[int]:
        """
        从字节数据直接匹配（无需预先解码）

        Args:
            bg_bytes: 背景图原始字节
            piece_bytes: 缺口图原始字节
            offset_correction: 坐标修正值

        Returns:
            缺口 X 坐标，失败返回 None
        """
        try:
            bg_arr = np.frombuffer(bg_bytes, np.uint8)
            bg_img = cv2.imdecode(bg_arr, cv2.IMREAD_COLOR)

            piece_arr = np.frombuffer(piece_bytes, np.uint8)
            piece_img = cv2.imdecode(piece_arr, cv2.IMREAD_COLOR)

            if bg_img is None or piece_img is None:
                logger.error("无法解码图片数据")
                return None

            return SliderImageMatcher.find_gap_position(
                bg_img, piece_img, offset_correction
            )
        except Exception as e:
            logger.error(f"从字节匹配失败: {e}")
            return None


# 便捷函数
find_gap = SliderImageMatcher.find_gap_position
find_gap_from_bytes = SliderImageMatcher.find_gap_from_bytes

__all__ = ['SliderImageMatcher', 'find_gap', 'find_gap_from_bytes']
