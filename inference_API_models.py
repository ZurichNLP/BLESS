#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# __Author__ = 'Dennis Aumiller'
# __Email__ = 'aumiller@informatik.uni-heidelberg.de'
# __Date__ = '2023-03-07'


import os
import sys
import time
from pathlib import Path
import random
import warnings
import logging
from typing import List, Dict, Tuple, Optional, Union

from tqdm import tqdm
from transformers import HfArgumentParser, set_seed
from langchain.llms import Cohere, OpenAI


from utils.helpers import iter_batches, iter_json_lines, serialize_to_jsonl, get_output_file_name, persist_args
from utils.prompting import prepare_prompted_inputs, RandomExampleSelector, postprocess_model_outputs, \
    construct_example_template, load_predefined_prompt
from llm_inference import InferenceArguments

from api_secrets import COHERE_API_KEY, OPENAI_API_KEY

logger = logging.getLogger(__name__)


def run_API_inference(args: InferenceArguments):
    """
    Runs inference of an API model specified in the arguments.
    Note that this uses only a subset of the attributes of InferenceArguments for compatibility reasons.
    """
    # set random seed everywhere for reproducibility
    set_seed(args.seed)

    # Verify that the supported API models are called

    if not (args.model_name_or_path.lower().startswith("cohere-") or \
       args.model_name_or_path.lower().startswith("openai-")):
        raise ValueError("Currently only Cohere and OpenAI models are supported! "
                         "Prefix your model name with either 'cohere-' or 'openai-'")
    else:
        provider = args.model_name_or_path.lower().split("-")[0]
        # Infer the model name by removing the leading provider name
        model_name = "-".join(args.model_name_or_path.lower().split("-")[1:])

        logger.info(f"Loading {provider} model")
        logger.info("Note that full reproducibility is not guaranteed for API models.")

        if provider == "cohere":
            llm = Cohere(model=model_name,
                         k=args.top_k,
                         p=args.top_p,
                         max_tokens=args.max_new_tokens,
                         frequency_penalty=args.frequency_penalty,
                         presence_penalty=args.presence_penalty,
                         temperature=args.temperature,
                         cohere_api_key=COHERE_API_KEY
                         )
        else:
            # TODO: Consider adjusting parameter `n` (number of generations) or `best_of`
            llm = OpenAI(model=model_name,
                         temperature=args.temperature,
                         max_tokens=args.max_new_tokens,
                         top_p=args.top_p,
                         frequency_penalty=args.frequency_penalty,
                         presence_penalty=args.presence_penalty,
                         openai_api_key=OPENAI_API_KEY
                         )

    # prepare few-shot examples
    examples = list(iter_json_lines(args.examples))
    logger.info(f"Few-shot examples will be sampled from {len(examples)} items")
    # initialise example selector object
    example_selector = RandomExampleSelector(
        examples=examples,  # the examples it has available to choose from.
        few_shot_n=args.few_shot_n,
        n_refs=args.n_refs,
    )
    # construct example prompt that is reused for all inputs (with different examples)
    example_prompt = construct_example_template(args.prompt_template, args.source_field, args.target_field)

    if args.batch_size > 1:
        warnings.warn("Batch size set to value >1. API models only support batch size of 1. "
                      "Will change batch size to 1 automatically.")
        args.batch_size = 1

    with open(args.output_file, "w", encoding="utf8") if args.output_file != "stdout" else sys.stdout as outf:
        start_time = time.time()
        c = 0  # counter for generated output sequences

        for batch in tqdm(iter_batches(args.input_file, args.batch_size)):
            # input file can be a text file or a jsonl file
            # if jsonl file, we expect that the input sentence is in the key specified by args.source_field
            if isinstance(batch[0], dict):
                batch_inputs = [i[args.source_field] for i in batch]
                batch_refs = [i[args.target_field] for i in batch]
            else:  # otherwise, we expect batch to be a list of strings
                batch_inputs = batch
                batch_refs = None

            # construct prompted inputs for each example in the batch
            inputs = prepare_prompted_inputs(
                inputs=batch_inputs,
                example_selector=example_selector,
                prefix=args.prompt_prefix,
                suffix=args.prompt_suffix,
                example_prompt=example_prompt,
                example_separator=args.example_separator,
                prompt_format=args.prompt_format,
            )

            outputs = llm(inputs[0])

            outputs = postprocess_model_outputs(inputs, [[outputs]], args.example_separator)

            for line in serialize_to_jsonl(inputs, outputs, batch_inputs, batch_refs):
                outf.write(f"{line}\n")
                c += 1

        end_time = time.time()
        logger.info(f"Finished inference on {args.input_file} in {end_time - start_time:.4f} seconds.")
        logger.info(f"Wrote {c} outputs to {args.output_file}")


if __name__ == '__main__':
    parser = HfArgumentParser((InferenceArguments))
    args = parser.parse_args_into_dataclasses()[0]

    # Use stdout when output_file and output_dir is not specified (e.g. for debugging)
    if not args.output_file and not args.output_dir:
        args.output_file = "stdout"
    elif args.output_file and not args.output_dir:
        args.output_file = Path(args.output_file)
        args.output_file.parent.mkdir(parents=True, exist_ok=True)
    elif not args.output_file and args.output_dir:
        args.output_file = get_output_file_name(args)
        Path(args.output_file).parent.mkdir(parents=True, exist_ok=True)
    else:
        raise RuntimeError(f"Could not infer output file!")

    # set up prompt
    args = load_predefined_prompt(args)
    # save all arguments to output file
    persist_args(args)

    run_API_inference(args)