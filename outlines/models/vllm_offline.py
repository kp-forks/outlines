"""Integration with the `vllm` library (offline mode)."""

import json
from functools import singledispatchmethod
from typing import TYPE_CHECKING, Any, List, Optional, Union

from outlines.inputs import Chat
from outlines.models.base import Model, ModelTypeAdapter
from outlines.models.openai import OpenAITypeAdapter
from outlines.types.dsl import CFG, JsonSchema, python_types_to_terms, to_regex

if TYPE_CHECKING:
    from vllm import LLM
    from vllm.sampling_params import SamplingParams

__all__ = ["VLLMOffline", "from_vllm_offline"]


class VLLMOfflineTypeAdapter(ModelTypeAdapter):
    """Type adapter for the `VLLMOffline` model."""

    @singledispatchmethod
    def format_input(self, model_input):
        """Generate the prompt argument to pass to the model.

        Argument
        --------
        model_input
            The input passed by the user.

        """
        raise TypeError(
            f"The input type {type(model_input)} is not available with "
            "VLLM offline. The only available types are `str` and "
            "`Chat` (containing a prompt and images)."
        )

    @format_input.register(str)
    def format_input_str(self, model_input: str) -> str:
        """Format a `str` input.

        """
        return model_input

    @format_input.register(Chat)
    def format_input_chat(self, model_input: Chat) -> list:
        """Format a `Chat` input.

        """
        for message in model_input.messages:
            content = message["content"]
            if isinstance(content, list):
                raise ValueError(
                    "Assets are not supported for vLLM offline."
                    "Please only use text content in the `Chat` input."
                )
        return OpenAITypeAdapter().format_input(model_input)

    def format_output_type(self, output_type: Optional[Any] = None) -> dict:
        """Generate the structured output argument to pass to the model.

        For vLLM, the structured output definition is set in the
        `GuidedDecodingParams` constructor that is provided as a value to the
        `guided_decoding` parameter of the `SamplingParams` constructor, itself
        provided as a value to the `sampling_params` parameter of the `generate`
        method.

        Parameters
        ----------
        output_type
            The structured output type provided.

        Returns
        -------
        dict
            The arguments to provide to the `GuidedDecodingParams` constructor.

        """
        if output_type is None:
            return {}

        term = python_types_to_terms(output_type)
        if isinstance(term, CFG):
            return {"grammar": term.definition}
        elif isinstance(term, JsonSchema):
            guided_decoding_params = {"json": json.loads(term.schema)}
            if term.whitespace_pattern:
                guided_decoding_params["whitespace_pattern"] = term.whitespace_pattern
            return guided_decoding_params
        else:
            return {"regex": to_regex(term)}


class VLLMOffline(Model):
    """Thin wrapper around a `vllm.LLM` model.

    This wrapper is used to convert the input and output types specified by the
    users at a higher level to arguments to the `vllm.LLM` model.

    """

    def __init__(self, model: "LLM"):
        """Create a VLLM model instance.

        Parameters
        ----------
        model
            A `vllm.LLM` model instance.

        """
        self.model = model
        self.type_adapter = VLLMOfflineTypeAdapter()

    def _build_generation_args(
        self,
        inference_kwargs: dict,
        output_type: Optional[Any] = None,
    ) -> "SamplingParams":
        """Create the `SamplingParams` object to pass to the `generate` method
        of the `vllm.LLM` model."""
        from vllm.sampling_params import GuidedDecodingParams, SamplingParams

        sampling_params = inference_kwargs.pop("sampling_params", None)

        if sampling_params is None:
            sampling_params = SamplingParams()

        output_type_args = self.type_adapter.format_output_type(output_type)
        if output_type_args:
            sampling_params.guided_decoding = GuidedDecodingParams(**output_type_args)

        return sampling_params

    def generate(
        self,
        model_input: Chat | str,
        output_type: Optional[Any] = None,
        **inference_kwargs: Any,
    ) -> Union[str, List[str]]:
        """Generate text using vLLM offline.

        Parameters
        ----------
        prompt
            The prompt based on which the model will generate a response.
        output_type
            The logits processor the model will use to constrain the format of
            the generated text.
        inference_kwargs
            Additional keyword arguments to pass to the `generate` method
            in the `vllm.LLM` model.

        Returns
        -------
        Union[str, List[str]]
            The text generated by the model.

        """
        sampling_params = self._build_generation_args(
            inference_kwargs,
            output_type,
        )

        if isinstance(model_input, Chat):
            results = self.model.chat(
                messages=self.type_adapter.format_input(model_input),
                sampling_params=sampling_params,
                **inference_kwargs,
            )
        else:
            results = self.model.generate(
                prompts=self.type_adapter.format_input(model_input),
                sampling_params=sampling_params,
                **inference_kwargs,
            )
        results = [completion.text for completion in results[0].outputs]

        if len(results) == 1:
            return results[0]
        else:
            return results

    def generate_batch(
        self,
        model_input: List[Chat | str],
        output_type: Optional[Any] = None,
        **inference_kwargs: Any,
    ) -> Union[List[str], List[List[str]]]:
        """Generate a batch of completions using vLLM offline.

        Parameters
        ----------
        prompt
            The list of prompts based on which the model will generate a
            response.
        output_type
            The logits processor the model will use to constrain the format of
            the generated text.
        inference_kwargs
            Additional keyword arguments to pass to the `generate` method
            in the `vllm.LLM` model.

        Returns
        -------
        Union[List[str], List[List[str]]]
            The text generated by the model.

        """
        sampling_params = self._build_generation_args(
            inference_kwargs,
            output_type,
        )

        if any(isinstance(item, Chat) for item in model_input):
            raise TypeError(
                "Batch generation is not available for the `Chat` input type."
            )

        results = self.model.generate(
            prompts=[self.type_adapter.format_input(item) for item in model_input],
            sampling_params=sampling_params,
            **inference_kwargs,
        )
        return [[sample.text for sample in batch.outputs] for batch in results]

    def generate_stream(self, model_input, output_type, **inference_kwargs):
        """Not available for `vllm.LLM`.

        TODO: Implement the streaming functionality ourselves.

        """
        raise NotImplementedError(
            "Streaming is not available for the vLLM offline integration."
        )


def from_vllm_offline(model: "LLM") -> VLLMOffline:
    """Create an Outlines `VLLMOffline` model instance from a `vllm.LLM`
    instance.

    Parameters
    ----------
    model
        A `vllm.LLM` instance.

    Returns
    -------
    VLLMOffline
        An Outlines `VLLMOffline` model instance.

    """
    return VLLMOffline(model)
