"""
Result/error handling effects.

This module provides Result effects for error handling.
"""

from typing import Any, Callable, Union

from .base import Effect, create_effect_with_trace


class result:
    """Result/error handling effects."""

    @staticmethod
    def fail(exc: Exception) -> Effect:
        """Signal failure."""
        return create_effect_with_trace("result.fail", exc)

    @staticmethod
    def catch(sub_program: Any, handler: Callable[[Exception], Any]) -> Effect:
        """Try sub-program with error handler.

        Args:
            sub_program: Program to try
            handler: Function to handle exceptions
        """
        return create_effect_with_trace("result.catch", {"program": sub_program, "handler": handler})
    
    @staticmethod
    def recover(sub_program: Any, fallback: Union[Any, Callable[[Exception], Any]]) -> Effect:
        """Try sub-program, use fallback value on error.
        
        Args:
            sub_program: Program to try
            fallback: Can be:
                - A direct value to use on error
                - A Program to run on error
                - A function (Exception) -> value/Program to handle the error
        """
        return create_effect_with_trace("result.recover", {"program": sub_program, "fallback": fallback})
    
    @staticmethod
    def retry(sub_program: Any, max_attempts: int = 3, delay_ms: int = 0) -> Effect:
        """Retry sub-program on failure.
        
        Args:
            sub_program: Program to retry
            max_attempts: Maximum number of attempts (default: 3)
            delay_ms: Delay between attempts in milliseconds (default: 0)
        """
        return create_effect_with_trace("result.retry", {
            "program": sub_program,
            "max_attempts": max_attempts,
            "delay_ms": delay_ms
        })


# Uppercase aliases
def Fail(exc: Exception) -> Effect:
    """Result: Signal failure."""
    return create_effect_with_trace("result.fail", exc, skip_frames=3)


def Catch(sub_program: Any, handler: Callable[[Exception], Any]) -> Effect:
    """Result: Try sub-program with error handler."""
    return create_effect_with_trace("result.catch", {"program": sub_program, "handler": handler}, skip_frames=3)


def Recover(sub_program: Any, fallback: Union[Any, Callable[[Exception], Any]]) -> Effect:
    """Result: Try sub-program, use fallback value on error.
    
    Args:
        sub_program: Program to try
        fallback: Can be:
            - A direct value to use on error
            - A Program to run on error
            - A function (Exception) -> value/Program to handle the error
    """
    return create_effect_with_trace("result.recover", {"program": sub_program, "fallback": fallback}, skip_frames=3)


def Retry(sub_program: Any, max_attempts: int = 3, delay_ms: int = 0) -> Effect:
    """Result: Retry sub-program on failure."""
    return create_effect_with_trace("result.retry", {
        "program": sub_program,
        "max_attempts": max_attempts,
        "delay_ms": delay_ms
    }, skip_frames=3)


__all__ = [
    "result",
    "Fail",
    "Catch",
    "Recover",
    "Retry",
]